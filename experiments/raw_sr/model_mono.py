import torch
import torch.nn as nn
from transformers import Swin2SRForImageSuperResolution

from model import BayerModelBase

class BayerSwin2SRMono(BayerModelBase):
    """
    Bayer → ×2 monochrome: BayerSwin2SR with a 1-channel final convolution.

    Input: (B, 4, H, W) packed Bayer (R, G1, G2, B). Output: (B, 1, 4H, 4W) monochrome.

    For black-and-white output. It minimises the error of the mix directly, where the RGB model
    minimises per-channel errors that only partly cancel when converted. It learns one mix,
    dataset.MONO_MIX in linear light; others (luminance, filters) come from scaling the input
    channels, dataset.mix_gains. Same network body, so about the same speed as the RGB model.
    """
    def __init__(
        self,
        pretrained_model_name: str = 'caidas/swin2SR-classical-sr-x4-64',
        random_first_conv: bool = False,
        random_final_conv: bool = False,
    ):
        super().__init__()
        base = Swin2SRForImageSuperResolution.from_pretrained(pretrained_model_name)
        self.config = base.config
        self.upscale = base.config.upscale  # 4x from packed Bayer = 2x from native resolution
        
        # 1. Adapt First Convolution: 4 input channels (R, G1, G2, B)
        old_first = base.swin2sr.first_convolution
        new_first = nn.Conv2d(
            4, old_first.out_channels,
            kernel_size=old_first.kernel_size,
            stride=old_first.stride,
            padding=old_first.padding
        )
        with torch.no_grad():
            if random_first_conv:
                nn.init.kaiming_normal_(new_first.weight, mode='fan_out', nonlinearity='relu')
                nn.init.constant_(new_first.bias, 0.0)
            else:
                new_first.weight[:, 0:1, :, :] = old_first.weight[:, 0:1, :, :]        # R
                new_first.weight[:, 1:2, :, :] = old_first.weight[:, 1:2, :, :] * 0.5  # G1
                new_first.weight[:, 2:3, :, :] = old_first.weight[:, 1:2, :, :] * 0.5  # G2
                new_first.weight[:, 3:4, :, :] = old_first.weight[:, 2:3, :, :]        # B
                new_first.bias.copy_(old_first.bias)
        base.swin2sr.first_convolution = new_first
        
        # 2. Adapt Final Convolution: 1 output channel (Monochrome Luminance)
        old_final = base.upsample.final_convolution
        new_final = nn.Conv2d(
            old_final.in_channels, 1,
            kernel_size=old_final.kernel_size,
            stride=old_final.stride,
            padding=old_final.padding
        )
        with torch.no_grad():
            if random_final_conv:
                nn.init.kaiming_normal_(new_final.weight, mode='fan_out', nonlinearity='linear')
                nn.init.constant_(new_final.bias, 0.0)
            else:
                # Initialize using standard perceptual Rec.709 luminance weights:
                # Y = 0.2989 * R + 0.5870 * G + 0.1140 * B
                luma_w = torch.tensor([0.2989, 0.5870, 0.1140], device=old_final.weight.device).view(3, 1, 1, 1)
                new_final.weight.copy_((old_final.weight * luma_w).sum(dim=0, keepdim=True))
                luma_b = torch.tensor([0.2989, 0.5870, 0.1140], device=old_final.bias.device)
                new_final.bias.copy_((old_final.bias * luma_b).sum(dim=0, keepdim=True))
        base.upsample.final_convolution = new_final

        # 3. Adapt Means
        rgb_mean = base.swin2sr.mean.data  # (1, 3, 1, 1)
        bayer_mean = torch.cat([rgb_mean[:, 0:1], rgb_mean[:, 1:2], rgb_mean[:, 1:2], rgb_mean[:, 2:3]], dim=1)
        base.swin2sr.register_buffer('mean', bayer_mean)
        
        luma_mean_w = torch.tensor([0.2989, 0.5870, 0.1140], device=rgb_mean.device).view(1, 3, 1, 1)
        mono_output_mean = (rgb_mean * luma_mean_w).sum(dim=1, keepdim=True)
        self.register_buffer('output_mean', mono_output_mean)

        self.swin2sr = base.swin2sr
        self.upsample = base.upsample
        self._setup_noise()

    def _run(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (B, 4, H, W) packed Bayer tensor.
        Returns:
            (B, 1, H * upscale, W * upscale) monochrome luminance image.
        """
        H, W = x.shape[2:]
        # Run through Swin Transformer body
        outputs = self.swin2sr(x)
        features = outputs[0]
        # PixelShuffle upsampling
        rec = self.upsample(features)
        # Un-normalize using the 1-channel output mean
        rec = rec / self.swin2sr.img_range + self.output_mean
        return rec[:, :, : H * self.upscale, : W * self.upscale]

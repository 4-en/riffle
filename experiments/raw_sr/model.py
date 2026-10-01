import torch
import torch.nn as nn
from transformers import Swin2SRForImageSuperResolution

class BayerSwin2SR(nn.Module):
    def __init__(self, pretrained_model_name='caidas/swin2SR-classical-sr-x4-64', random_first_conv=False):
        super().__init__()
        base = Swin2SRForImageSuperResolution.from_pretrained(pretrained_model_name)
        self.config = base.config
        self.upscale = base.config.upscale
        
        # Replace first convolution to accept 4 channels (R, G1, G2, B) instead of 3 (R, G, B)
        old_conv = base.swin2sr.first_convolution
        new_conv = nn.Conv2d(4, old_conv.out_channels, 
                             kernel_size=old_conv.kernel_size, 
                             stride=old_conv.stride, 
                             padding=old_conv.padding)
        with torch.no_grad():
            if random_first_conv:
                nn.init.kaiming_normal_(new_conv.weight, mode='fan_out', nonlinearity='relu')
                nn.init.constant_(new_conv.bias, 0.0)
            else:
                new_conv.weight[:, 0:1, :, :] = old_conv.weight[:, 0:1, :, :]      # R
                new_conv.weight[:, 1:2, :, :] = old_conv.weight[:, 1:2, :, :] * 0.5  # G1
                new_conv.weight[:, 2:3, :, :] = old_conv.weight[:, 1:2, :, :] * 0.5  # G2
                new_conv.weight[:, 3:4, :, :] = old_conv.weight[:, 2:3, :, :]      # B
                new_conv.bias.copy_(old_conv.bias)
        base.swin2sr.first_convolution = new_conv
        
        # Swin2SR expects inputs around a mean. Base uses a 3-channel mean.
        # We adapt it to a 4-channel mean for the Bayer input.
        rgb_mean = base.swin2sr.mean.data # (1, 3, 1, 1)
        bayer_mean = torch.cat([rgb_mean[:, 0:1], rgb_mean[:, 1:2], rgb_mean[:, 1:2], rgb_mean[:, 2:3]], dim=1)
        base.swin2sr.register_buffer('mean', bayer_mean)
        self.register_buffer('output_mean', rgb_mean)
        
        self.swin2sr = base.swin2sr
        self.upsample = base.upsample
        
    def forward(self, x):
        """
        x: (B, 4, H, W) packed Bayer tensor.
        Returns: (B, 3, H * upscale, W * upscale) RGB tensor.
        """
        H, W = x.shape[2:]
        # Run through Swin Transformer body
        outputs = self.swin2sr(x)
        features = outputs[0]
        # Upsample
        rec = self.upsample(features)
        # Un-normalize using the 3-channel output mean
        rec = rec / self.swin2sr.img_range + self.output_mean
        return rec[:, :, : H * self.upscale, : W * self.upscale]

    def save_checkpoint(self, path):
        torch.save(self.state_dict(), path)
        
    def load_checkpoint(self, path):
        self.load_state_dict(torch.load(path, map_location="cpu"))

"""A lightweight Bayer → ×2 model, to compare with the Swin2SR one (model.py), kept separate.

Same interface: forward(x, noise) with x the packed Bayer data (B, 4, H, W; white-balanced,
scaled to its peak, gamma 2.2) and noise the log2 noise level to remove (see
model.BayerModelBase); returns (B, C, 4H, 4W), C = 3 (colour) or 1 (black and white).

- SPAN (Swift Parameter-free Attention Network; spandrel's implementation), built with 4
  input channels and ×4 out, its RGB normalisation off (the input is centred here instead).
  Its 3×3 convolutions are trained as three and merged into one in eval mode.
- Residual: the network predicts the difference to a bilinear enlargement of the quads
  (R, the mean of the greens, B; for black and white their (R + 2G + B) / 4), which a small
  network trained from scratch learns faster than the whole image.
- The noise input as in the Swin2SR model: an offset on the first convolution's features,
  zero at first.

Checkpoints are dicts: {"arch", "channels", "out_channels", "state_dict"}.
"""

import time

import torch
import torch.nn.functional as F
from torch import nn

ARCHS = ("span",)


class NoiseEmbedding(nn.Module):
    def __init__(self, dim, hidden=64):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(1, hidden), nn.SiLU(), nn.Linear(hidden, dim))
        nn.init.zeros_(self.net[2].weight)
        nn.init.zeros_(self.net[2].bias)

    def forward(self, noise):
        return self.net(noise.reshape(-1, 1).float())[:, :, None, None]


class LightBayerSR(nn.Module):
    def __init__(self, arch="span", channels=48, out_channels=3):
        super().__init__()
        if arch not in ARCHS:
            raise ValueError(f"arch must be one of {', '.join(ARCHS)}")
        from spandrel.architectures.SPAN import SPAN

        self.arch, self.channels, self.out_channels = arch, channels, out_channels
        self.net = SPAN(num_in_ch=4, num_out_ch=out_channels, feature_channels=channels, upscale=4, norm=False)
        self.noise_embed = NoiseEmbedding(channels)
        self._noise = None
        self.net.conv_1.register_forward_hook(self._add_noise)

    def _add_noise(self, module, args, out):
        return out if self._noise is None else out + self.noise_embed(self._noise).to(out.dtype)

    def base(self, x):
        """The bilinear enlargement the network adds to."""
        rgb = torch.stack([x[:, 0], 0.5 * (x[:, 1] + x[:, 2]), x[:, 3]], dim=1)
        if self.out_channels == 1:
            rgb = 0.25 * rgb[:, :1] + 0.5 * rgb[:, 1:2] + 0.25 * rgb[:, 2:]
        return F.interpolate(rgb, scale_factor=4, mode="bilinear", align_corners=False)

    def forward(self, x, noise=None):
        self._noise = noise
        try:
            return self.base(x) + self.net(x - 0.5)
        finally:
            self._noise = None

    def checkpoint(self):
        return {"arch": self.arch, "channels": self.channels, "out_channels": self.out_channels,
                "state_dict": self.state_dict()}


def load_light(path, device="cpu"):
    ck = torch.load(path, map_location="cpu", weights_only=True)
    model = LightBayerSR(ck["arch"], ck["channels"], ck["out_channels"])
    model.load_state_dict(ck["state_dict"])
    return model.to(device).eval()


@torch.no_grad()
def seconds_per_frame(model, packed_hw=(1956, 2620), tile=256, pad=24, device="cuda"):
    """Seconds the network takes for a 20 MP RAW (packed 1956 × 2620), tiled as at inference,
    fp16, measured on a few tiles and scaled up (excludes reading and writing)."""
    model = model.to(device).eval()
    n_tiles = -(-packed_hw[0] // tile) * -(-packed_hw[1] // tile)
    side = tile + 2 * pad
    x = torch.rand(1, 4, side, side, device=device)
    noise = torch.zeros(1, device=device)
    with torch.autocast(device, dtype=torch.float16):
        for _ in range(2):
            model(x, noise)  # warm up
        torch.cuda.synchronize()
        t = time.perf_counter()
        for _ in range(5):
            model(x, noise)
        torch.cuda.synchronize()
    return (time.perf_counter() - t) / 5 * n_tiles

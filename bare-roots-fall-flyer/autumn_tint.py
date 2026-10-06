# Shift green foliage toward fall yellows/oranges.
# Usage: python3 autumn_tint.py SRC DST STRENGTH SEED [DAMP_START] [DAMP_AMOUNT]
#   photo-1: 1.0 3 0.55 0.55   photo-2: 1.0 7 0.38 0.92 (protects the lawn)
import sys, numpy as np
from PIL import Image, ImageFilter
src, dst, strength, seed = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
d0 = float(sys.argv[5]) if len(sys.argv)>5 else 0.55
dk = float(sys.argv[6]) if len(sys.argv)>6 else 0.55
im = Image.open(src).convert("RGB")
hsv = np.asarray(im.convert("HSV")).astype(np.float32)
h, s, v = hsv[...,0]*360/255, hsv[...,1]/255, hsv[...,2]/255
H, W = h.shape
# low-frequency patch field so some foliage turns yellow, some orange, some stays green
rng = np.random.default_rng(seed)
f = Image.fromarray((rng.random((max(2,H//60), max(2,W//60)))*255).astype(np.uint8)).resize((W,H), Image.BICUBIC)
f = np.asarray(f.filter(ImageFilter.GaussianBlur(W/40))).astype(np.float32)/255
f = (f - f.min())/(f.max()-f.min()+1e-6)
# green foliage mask, feathered by hue distance and requiring some saturation
green = np.clip(1.3 - np.abs(h - 100)/55, 0, 1) * np.clip((s-0.12)/0.15, 0, 1)
# grass tends to be brighter and lower in frame; foliage gets the full effect, lawn less
rows = np.linspace(0,1,H)[:,None]
lawn_damp = 1 - dk*np.clip((rows-d0)/0.12,0,1)
amt = green * strength * (0.35 + 0.65*f) * lawn_damp
target = 48 - 22*f          # yellow (~48°) to orange (~26°)
h2 = h + (target - h)*amt
s2 = np.clip(s*(1+0.12*amt), 0, 1)
v2 = np.clip(v*(1+0.04*amt), 0, 1)
out = np.stack([h2/360*255, s2*255, v2*255], -1).astype(np.uint8)
res = Image.fromarray(out, "HSV").convert("RGB")
# gentle overall warm grade
r = np.asarray(res).astype(np.float32)
r[...,0]*=1.03; r[...,2]*=0.95
Image.fromarray(np.clip(r,0,255).astype(np.uint8)).save(dst, quality=92)

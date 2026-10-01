import cv2
import numpy as np
from pathlib import Path

def variance_of_laplacian(image):
    return cv2.Laplacian(image, cv2.CV_64F).var()

def main():
    out_dir = Path("experiments/raw_sr/comparison_crops")
    out_dir.mkdir(exist_ok=True)
    
    dt_img = cv2.imread("compare_out_honest/P5290599.tif")
    base_img = cv2.imread("compare_out_honest/out_baseline_no_geom.tif")
    bayer_img = cv2.imread("compare_out_honest/out_bayer.tif")
    
    if any(i is None for i in [dt_img, base_img, bayer_img]):
        print("Missing TIFs.")
        return
        
    H, W = bayer_img.shape[:2]
    dt_2x = cv2.resize(dt_img, (W, H), interpolation=cv2.INTER_NEAREST)
    
    # We will find sharp patches on the 2x Baseline image as it has high contrast
    gray = cv2.cvtColor(base_img, cv2.COLOR_BGR2GRAY)
    
    size = 400
    stride = size // 2
    patches = []
    
    print("Scanning for the sharpest, highest-contrast patches...")
    for y in range(0, H - size, stride):
        for x in range(0, W - size, stride):
            patch = gray[y:y+size, x:x+size]
            var = variance_of_laplacian(patch)
            patches.append((var, x, y))
            
    patches.sort(reverse=True, key=lambda x: x[0])
    
    # Filter overlaps
    filtered_centers = []
    for var, x, y in patches:
        overlap = False
        for fx, fy in filtered_centers:
            if abs(x - fx) < size and abs(y - fy) < size:
                overlap = True
                break
        if not overlap:
            filtered_centers.append((x, y))
            if len(filtered_centers) >= 3:
                break
                
    for i, (cx, cy) in enumerate(filtered_centers):
        c_dt = dt_2x[cy:cy+size, cx:cx+size]
        c_base = base_img[cy:cy+size, cx:cx+size]
        c_bayer = bayer_img[cy:cy+size, cx:cx+size]
        
        combined = np.concatenate([c_dt, c_base, c_bayer], axis=1)
        
        font = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(combined, "Darktable (Nearest 2x)", (10, 30), font, 0.7, (255,255,255), 2)
        cv2.putText(combined, "Baseline Upscale 2x", (size + 10, 30), font, 0.7, (255,255,255), 2)
        cv2.putText(combined, "Bayer-Swin2SR 2x", (2*size + 10, 30), font, 0.7, (255,255,255), 2)
        
        out_file = out_dir / f"sharp_crop_{i}.jpg"
        cv2.imwrite(str(out_file), combined)
        print(f"Saved {out_file} (Laplacian Variance: {patches[i][0]:.2f})")

if __name__ == "__main__":
    main()

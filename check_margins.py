import rawpy
import sys
with rawpy.imread(sys.argv[1]) as raw:
    print(f"raw_image shape: {raw.raw_image.shape}")
    rgb = raw.postprocess(user_flip=0)
    print(f"postprocess shape: {rgb.shape}")
    print(f"sizes: top={raw.sizes.top_margin}, left={raw.sizes.left_margin}, width={raw.sizes.width}, height={raw.sizes.height}")
    print(f"flip: {raw.sizes.flip}")

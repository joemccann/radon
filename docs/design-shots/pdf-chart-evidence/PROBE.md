# Synthetic probe output, research.figures.detect on main @ e5674ccc, pypdfium2 5.13.0

```

== A. vector chart, MediaBox (0,0,612,792), no rotation [control]
   page: {'size': (612.0, 792.0), 'bbox': (0.0, 0.0, 612.0, 792.0), 'rotation': 0, 'drawn': {2: 11}}
   detect -> 1 figure(s): [(10, [0.0761, 0.099, 0.8892, 0.5365])]

== B. single raster IMAGE XObject chart (H2: MIN_OBJECTS=3)
   page: {'size': (612.0, 792.0), 'bbox': (0.0, 0.0, 612.0, 792.0), 'rotation': 0, 'drawn': {3: 1}}
   detect -> 0 figure(s): []

== C. vector chart, MediaBox origin (10,10) (H1)
   page: {'size': (612.0, 792.0), 'bbox': (10.0, 10.0, 622.0, 802.0), 'rotation': 0, 'drawn': {2: 11}}
   detect -> 0 figure(s): []

== D. vector chart, CropBox inset 20pt, MediaBox zero-origin (H1 variant)
   page: {'size': (572.0, 752.0), 'bbox': (20.0, 20.0, 592.0, 772.0), 'rotation': 0, 'drawn': {2: 11}}
   detect -> 0 figure(s): []

== E. vector chart, /Rotate 90 (H1)
   page: {'size': (792.0, 612.0), 'bbox': (0.0, 0.0, 612.0, 792.0), 'rotation': 90, 'drawn': {2: 11}}
   detect -> 0 figure(s): []

== F. two side-by-side IMAGE charts, each a lone object
   page: {'size': (612.0, 792.0), 'bbox': (0.0, 0.0, 612.0, 792.0), 'rotation': 0, 'drawn': {3: 2}}
   detect -> 0 figure(s): []

== G. IMAGE + 2 axis paths (=3 objects; passes MIN_OBJECTS)
   page: {'size': (612.0, 792.0), 'bbox': (0.0, 0.0, 612.0, 792.0), 'rotation': 0, 'drawn': {3: 1, 2: 2}}
   detect -> 1 figure(s): [(3, [0.1108, 0.1348, 0.8892, 0.5016])]
C_mediabox_offset: bbox=(10.0, 10.0, 622.0, 802.0)
   objects normalized against bbox -> [0.1, 0.153, 0.867, 0.508]
   dark pixels in render (excl. header)  -> [0.066, 0.121, 0.865, 0.616]
D_cropbox: bbox=(20.0, 20.0, 592.0, 772.0)
   objects normalized against bbox -> [0.09, 0.122, 0.91, 0.495]
   dark pixels in render (excl. header)  -> [0.053, 0.12, 0.908, 0.608]
A_vector_baseline: bbox=(0.0, 0.0, 612.0, 792.0)
   objects normalized against bbox -> [0.117, 0.141, 0.883, 0.496]
   dark pixels in render (excl. header)  -> [0.083, 0.139, 0.882, 0.603]
```

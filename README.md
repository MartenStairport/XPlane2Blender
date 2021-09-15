

# Introduction
This script is a modified version of the original XPlane2Blender exporter by Laminar Research.

## Feature Auto LOD
In order to speed up the workflow of creating different LOD levels for an object we implemented a new function to

- automatically create 4 LOD levels of an object
- calculate LOD level distances based on the objects dimension
- automatically assign all required settings to export the object including all LOD levels

How to use:

1. Create a collection and name it like your exported object
2. Add the object to the collection
3. In scene properties activate the root collection for export, assign textures and all other properties you need for that object (make sure to name the collection exactly like in the scene) 
4. Select the object you want to have LOD levels for
5. In the Levels of Detail section of the root collection in scene properties click on the new "Generate 4-levels LOD" button.
6. In scene properties click "Export OBJs". Your object should now have 4 LOD levels

It will generate 3 additional objects with proper naming scaling down the poly count by using the decimate modifier. Additionally the LOD distances and the corresponding LOD level will be assigned to all 4 objects


## No Support
This is an unofficial mod and is not being supported by Laminar or us as we only use it in our internal workflow. However as it might be useful to you we thought to share it. Enjoy!

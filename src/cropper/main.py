from SpaceCropping import crop_hdf5_file, HDF5Cropper

# 简单使用
# crop_hdf5_file('2A.GPM.DPR.V9-20240130.20250101-S021033-E034346.061566.V07C.HDF5', 'output3.hdf5',
#                10, 20, 157, 160, 'Latitude', 'Longitude',
#                ['binBBBottom'], data_group="FS/CSF", latlon_group="FS", verbose=True)

#面向对象使用
cropper = HDF5Cropper(verbose=False)
cropper.crop_file('/Users/crocotear/Documents/挑战者杯/data/hdf5/2A.GPM.Ka.V9-20211125.20230101-S231026-E004258.050253.V07A.HDF5',
                  'out/2A.GPM.Ka.V9-20211125.20230101-S231026-E004258.050253.V07A_cropped_lat-58to-48_lon102to142.h5', -58, -48, 102, 142, 'Latitude', 'Longitude',data_group="FS/CSF",latlon_group="FS")
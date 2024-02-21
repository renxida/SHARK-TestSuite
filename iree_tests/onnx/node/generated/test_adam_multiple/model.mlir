module {
  func.func @test_adam_multiple(%arg0: !torch.vtensor<[],f32>, %arg1: !torch.vtensor<[],si64>, %arg2: !torch.vtensor<[1],f32>, %arg3: !torch.vtensor<[2],f32>, %arg4: !torch.vtensor<[1],f32>, %arg5: !torch.vtensor<[2],f32>, %arg6: !torch.vtensor<[1],f32>, %arg7: !torch.vtensor<[2],f32>, %arg8: !torch.vtensor<[1],f32>, %arg9: !torch.vtensor<[2],f32>) -> (!torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>, !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>, !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>) attributes {torch.onnx_meta.ir_version = 7 : si64, torch.onnx_meta.opset_versions = {ai.onnx.preview.training = 1 : si64}, torch.onnx_meta.producer_name = "backend-test", torch.onnx_meta.producer_version = ""} {
    %0:6 = torch.operator "onnx.Adam"(%arg0, %arg1, %arg2, %arg3, %arg4, %arg5, %arg6, %arg7, %arg8, %arg9) {torch.onnx.alpha = 0.949999988 : f32, torch.onnx.beta = 8.500000e-01 : f32, torch.onnx.norm_coefficient = 1.000000e-03 : f32} : (!torch.vtensor<[],f32>, !torch.vtensor<[],si64>, !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>, !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>, !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>, !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>) -> (!torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>, !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>, !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>)
    return %0#0, %0#1, %0#2, %0#3, %0#4, %0#5 : !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>, !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>, !torch.vtensor<[1],f32>, !torch.vtensor<[2],f32>
  }
}


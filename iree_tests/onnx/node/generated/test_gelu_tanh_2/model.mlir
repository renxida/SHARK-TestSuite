module {
  func.func @test_gelu_tanh_2(%arg0: !torch.vtensor<[3,4,5],f32>) -> !torch.vtensor<[3,4,5],f32> attributes {torch.onnx_meta.ir_version = 9 : si64, torch.onnx_meta.opset_version = 20 : si64, torch.onnx_meta.producer_name = "backend-test", torch.onnx_meta.producer_version = ""} {
    %0 = torch.operator "onnx.Gelu"(%arg0) {torch.onnx.approximate = "tanh"} : (!torch.vtensor<[3,4,5],f32>) -> !torch.vtensor<[3,4,5],f32>
    return %0 : !torch.vtensor<[3,4,5],f32>
  }
}


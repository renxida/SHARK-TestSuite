import os, time, glob, sys, zipfile
from multiprocessing import Pool
import argparse
import numpy as np
import torch, io
import struct

# Put full path to your Torch MLIR ( https://github.com/llvm/torch-mlir ) build
# This can be overwritten by command line
TORCH_MLIR_BUILD = ""
# Put full path to your IREE build dir
# if you are not targeting AMD AIE, then IREE build of https://github.com/openxla/iree is fine
# Else set it to build dir of your build of https://github.com/nod-ai/iree-amd-aie
# This can be overwritten by comamand line
IREE_BUILD = ""


def getTestsList(frameworkname, test_types):
    testsList = []
    for test_type in test_types:
        globpattern = frameworkname + "/" + test_type + "/*"
        testsList += glob.glob(globpattern)
    return testsList


def changeToTestDir(run_dir):
    try:
        # If directory does not exist, make it
        os.makedirs(run_dir, exist_ok=True)
        os.chdir(run_dir)
        return 0
    except OSError as errormsg:
        print(
            "Could not make or change to test run directory",
            run_dir,
            " Error message: ",
            errormsg,
        )
        return 1


def launchCommand(args, scriptcommand, commandslog):
    if args.verbose:
        print("Launching:", scriptcommand, "[ Proc:", os.getpid(), "]")
    try:
        commandslog.write(scriptcommand)
        commandslog.write("\n")
        ret = os.system(scriptcommand)
        return ret
    except OSError as errormsg:
        print(
            "Invoking ",
            scriptcommand,
            " failed:",
            errormsg,
        )
        return 1


def concatenateFiles(inpfile1, inpfile2, outfile):
    f1 = open(inpfile1, "r")
    f2 = open(inpfile2, "r")
    ofile = open(outfile, "w")
    ofile.write(f1.read() + f2.read())
    f1.close()
    f2.close()
    ofile.close()


def logAndReturn(commandslog, timelog, resultdict, retval):
    for i in resultdict:
        listitem = [i] + resultdict[i]
        print(listitem, file=timelog)
    timelog.close()
    commandslog.close()
    return retval


def unzipONNXFile(testName, abs_directory, unzipped_file_name):
    # Look for any unzipped file and if there is not already an unzipped file
    # then first time unzip it.
    abs_unzip_file_name = abs_directory + "/" + unzipped_file_name
    abs_zip_file_name = abs_unzip_file_name + ".zip"
    # this test dir does not have a zipped test file, so nothing to do
    if not os.path.exists(abs_zip_file_name):
        return 0
    # if not already unzipped, then
    if not os.path.exists(abs_unzip_file_name):
        if not os.access(abs_directory, os.W_OK):
            print(
                "The directory",
                abs_directory,
                "is not writeable. Could not unzip",
                abs_zip_file_name,
            )
            return 1
        with zipfile.ZipFile(abs_zip_file_name, "r") as zip_ref:
            zip_ref.extractall(abs_directory)

    return 0


def getTestKind(testName):
    # extract second last name in test name and if that is "models" and
    # it may have zipped onnx files, unzip them if not already done so
    second_last_name_inpath = os.path.split(os.path.split(testName)[0])[1]
    return second_last_name_inpath


def loadTorchSave(filename):
    with open(filename, "rb") as f:
        bindata = f.read()
    buf = io.BytesIO(bindata)
    loaded = torch.load(buf)
    return loaded


def getShapeString(tensorvalue, dtype):
    inputshape = list(tensorvalue.shape)
    inputshapestring = "x".join([str(item) for item in inputshape])
    if dtype == "bf16":
        inputshapestring += "xbf16"
    else:
        inputshapestring += "xf32"
    return inputshapestring


def unpackBytearray(barray, num_elem, dtype):
    num_array = None
    if dtype == torch.int64:
        num_array = struct.unpack("q" * num_elem, barray)
    elif dtype == torch.float32 or dtype == torch.float:
        num_array = struct.unpack("f" * num_elem, barray)
    elif dtype == torch.bfloat16 or dtype == torch.float16 or dtype == torch.int16:
        num_array = struct.unpack("h" * num_elem, barray)
    elif dtype == torch.int8:
        num_array = struct.unpack("b" * num_elem, barray)
    else:
        print("In unpackBytearray, found an unsupported data type", dtype)
    rettensor = torch.tensor(num_array, dtype=dtype)
    return rettensor


def loadRawBinaryAsTorchSensor(binaryfile, shape, dtype):
    # Read the whole files as bytes
    with open(binaryfile, "rb") as f:
        binarydata = f.read()
    # Number of elements in tensor
    num_elem = torch.prod(torch.tensor(list(shape)))
    # Total bytes
    tensor_num_bytes = (num_elem * dtype.itemsize).item()
    barray = bytearray(binarydata[0:tensor_num_bytes])
    rettensor = unpackBytearray(barray, num_elem, dtype)
    reshapedtensor = rettensor.reshape(list(shape))
    return reshapedtensor


def packTensor(modelinput):
    mylist = modelinput.flatten().tolist()
    dtype = modelinput.dtype
    if dtype == torch.int64:
        bytearr = struct.pack("%sq" % len(mylist), *mylist)
    if dtype == torch.float32 or dtype == torch.float:
        bytearr = struct.pack("%sf" % len(mylist), *mylist)
    elif dtype == torch.bfloat16 or dtype == torch.float16 or dtype == torch.int16:
        bytearr = struct.pack("%sh" % len(mylist), *mylist)
    elif dtype == torch.int8:
        bytearr = struct.pack("%sb" % len(mylist), *mylist)
    else:
        print("In packTensor, found an nsupported data type", dtype)
    return bytearr


def writeInferenceInputBinFile(modelinput, modelinputbinfilename):
    with open(modelinputbinfilename, "wb") as f:
        bytearr = packTensor(modelinput)
        f.write(bytearr)
        f.close()


def runInference(
    curphase,
    testName,
    args,
    vmfbfilename,
    modelinputptfilename,
    goldoutputptfilename,
    infoutputfilename,
    scriptcommand,
    commandslog,
    timelog,
    resultdict,
):
    # read the gold output produced by model
    logfilename = "inference.log"
    modelinput = loadTorchSave(modelinputptfilename)
    goldoutput = loadTorchSave(goldoutputptfilename)
    inputarg = ""
    # If there is no input the do not pass --input
    if modelinput.numel() > 0:
        modelinputbinfilename = "inference_input.bin"
        writeInferenceInputBinFile(modelinput, modelinputbinfilename)
        inputshapestring = getShapeString(modelinput, args.dtype)
        inputarg = ' --input="' + inputshapestring + "=@" + modelinputbinfilename + '"'

    outputshapestring = getShapeString(goldoutput, args.dtype)
    outputarg = " --output=@" + infoutputfilename + " "
    scriptcommand = (
        SHARED_IREE_BUILD
        + "/tools/iree-run-module --module="
        + vmfbfilename
        + inputarg
        + outputarg
        + " > "
        + logfilename
        + " 2>&1"
    )

    start = time.time()

    if launchCommand(args, scriptcommand, commandslog):
        print("Test", testName, "failed[" + curphase + "]")
        return logAndReturn(commandslog, timelog, resultdict, 1)
    end = time.time()
    outputshape = goldoutput.size()
    torchdtype = goldoutput.dtype
    infoutput = loadRawBinaryAsTorchSensor(infoutputfilename, outputshape, torchdtype)
    if args.verbose:
        inerencelog = open(logfilename, "a")
        print("Gold reference:\n", goldoutput, file=inerencelog)
        print("Output from target hardware:\n", infoutput, file=inerencelog)

    goldoutput = goldoutput.flatten()
    infoutput = infoutput.flatten()
    # print("After flatten")
    inferencematched = False
    # if shapes do not match, we have a problem as comparison routines may crash
    # so gauard it
    if infoutput.shape != goldoutput.shape:
        inferencematched = False
    else:
        if args.zerotolerance:
            # If each element matches exactly only then torch.equal is true
            inferencematched = torch.equal(infoutput, goldoutput)
        else:
            rtol = 1e-03
            atol = 1e-03
            inferencematched = torch.allclose(
                infoutput, goldoutput, rtol=rtol, atol=atol, equal_nan=False
            )

    if not inferencematched:
        failedinflog = open("failedinference.log", "w")
        print("Gold reference:\n", goldoutput, file=failedinflog)
        print("Output from target hardware:\n", infoutput, file=failedinflog)
        print("Test", testName, "failed[output-mismatch]")
        return logAndReturn(commandslog, timelog, resultdict, 1)

    resultdict[curphase] = ["passed", end - start]


def runTest(aTuple):
    curdir = os.getcwd()
    # Do not construct absolute path here as this will run
    # in a new process and cur dir may change over time giving
    # unpredicatble results
    (frameworkname, testName, args, script_dir, run_dir) = aTuple
    testargs = " --dtype " + args.dtype
    testRunDir = run_dir + "/" + testName
    modelname = os.path.basename(testName)
    modelinputptfilename = testRunDir + "/" + modelname + "." + args.dtype + ".input.pt"
    goldoutputptfilename = (
        testRunDir + "/" + modelname + "." + args.dtype + ".goldoutput.pt"
    )
    infoutputfilename = testRunDir + "/" + modelname + "." + args.dtype + ".output.bin"
    phases = ["model-run", "onnx-import", "torch-mlir", "ireecompile", "inference"]
    resultdict = {}
    for phase in phases:
        # Put status and time taken for each phase
        resultdict[phase] = ["notrun", 0.0]

    testAbsPath = script_dir + "/" + testName

    toolsDirAbsPath = script_dir + "/tools"
    stubrunmodelpy = toolsDirAbsPath + "stubs/pytorchmodel.py"
    modelpy = testAbsPath + "/model.py"
    # This is the generated runmodel.py which will be run
    runmodelpy = "runmodel.py"
    # For args.framework == onnx, onnx is starting point, so mode is
    # forced to onnx if it is direct
    mode = args.mode

    if args.verbose:
        print("Running:", testName, "[ Proc:", os.getpid(), "]")
    if changeToTestDir(testRunDir):
        return 1

    # Open files to log commands run and time taken
    commandslog = open("commands.log", "w")
    timelog = open("time.log", "w")

    # start phases[0]
    curphase = phases[0]
    stubrunmodelpy = toolsDirAbsPath + "/stubs/pytorchmodel.py"
    onnxfilename = modelname + "." + args.dtype + ".onnx"
    # Concatenate the testName model.py and tools/runmodel.py as run.py to
    # form runnable script.
    if frameworkname == "onnx":
        # For onnx, dierct and onnx means same as direct generates/has onnx itself
        if mode == "direct":
            mode = "onnx"
        stubrunmodelpy = toolsDirAbsPath + "/stubs/onnxmodel.py"
        onnxfilename = "model.onnx"
        if getTestKind(testName) == "models":
            onnxfilename = testAbsPath + "/model.onnx"
            # Create soft link to the model.onnx
            unzipONNXFile(testName, testAbsPath, "model.onnx")
            if not os.path.exists("model.onnx"):
                os.symlink(onnxfilename, "model.onnx")

    concatenateFiles(modelpy, stubrunmodelpy, runmodelpy)
    testargs += " --mode " + mode + " --outfileprefix " + modelname
    logfilename = modelname + ".log"
    scriptcommand = (
        "python " + runmodelpy + " " + testargs + " 1> " + logfilename + " 2>&1"
    )
    start = time.time()
    if launchCommand(args, scriptcommand, commandslog):
        print("Test", testName, "failed[" + curphase + "]")
        return logAndReturn(commandslog, timelog, resultdict, 1)
    end = time.time()
    resultdict[curphase] = ["passed", end - start]

    torchmlirfilename = modelname + "." + args.dtype + ".pytorch.torch.mlir"

    if mode == "onnx" or mode == "ort":
        # start phases[1]
        curphase = phases[1]
        # Import ONNX into torch MLIR as torch.operator custom OP
        torchonnxfilename = modelname + "." + args.dtype + ".torch-onnx.mlir"
        logfilename = "torch-onnx.log"
        scriptcommand = (
            "python -m torch_mlir.tools.import_onnx "
            + onnxfilename
            + " -o "
            + torchonnxfilename
            + " 1> "
            + logfilename
            + " 2>&1"
        )
        start = time.time()

        if launchCommand(args, scriptcommand, commandslog):
            print("Test", testName, "failed[" + curphase + "]")
            return logAndReturn(commandslog, timelog, resultdict, 1)
        end = time.time()
        resultdict[curphase] = ["passed", end - start]

        # Lower torch ONNX to torch MLIR
        # start phases[2]
        curphase = phases[2]
        torchmlirfilename = modelname + "." + args.dtype + ".onnx.torch.mlir"
        logfilename = "onnxtotorch.log"
        commandstring = "/bin/torch-mlir-opt -convert-torch-onnx-to-torch "
        if args.torchtolinalg:
            commandstring += "-convert-torch-to-linalg "

        # TORCH_MLIR_BUILD = path_config["TORCH_MLIR_BUILD"]
        # print(f"In RunTest - torch mlir build - {SHARED_TORCH_MLIR_BUILD}")
        scriptcommand = (
            SHARED_TORCH_MLIR_BUILD
            + commandstring
            + torchonnxfilename
            + " > "
            + torchmlirfilename
            + " 2>"
            + logfilename
        )

        start = time.time()
        if launchCommand(args, scriptcommand, commandslog):
            print("Test", testName, "failed[" + curphase + "]")
            return logAndReturn(commandslog, timelog, resultdict, 1)
        end = time.time()
        resultdict[curphase] = ["passed", end - start]

    if args.upto == "torch-mlir":
        print("Test", testName, "passed")
        return logAndReturn(commandslog, timelog, resultdict, 0)

    # Compile torch MLIR using IREE to binary to target backend
    curphase = phases[3]
    vmfbfilename = modelname + "." + args.dtype + ".vfmb"
    logfilename = "ireecompile.log"
    # print(f"In RunTest - iree build - {SHARED_IREE_BUILD}")
    scriptcommand = (
        SHARED_IREE_BUILD
        + "/tools/iree-compile --iree-hal-target-backends="
        + args.backend
        + " "
        + torchmlirfilename
        + " > "
        + vmfbfilename
        + " 2>"
        + logfilename
    )
    start = time.time()
    if launchCommand(args, scriptcommand, commandslog):
        print("Test", testName, "failed[" + curphase + "]")
        return logAndReturn(commandslog, timelog, resultdict, 1)
    end = time.time()
    resultdict[curphase] = ["passed", end - start]

    if args.upto == "ireecompile":
        print("Test", testName, "passed")
        return logAndReturn(commandslog, timelog, resultdict, 0)

    # run inference now
    curphase = phases[4]
    if runInference(
        curphase,
        testName,
        args,
        vmfbfilename,
        modelinputptfilename,
        goldoutputptfilename,
        infoutputfilename,
        scriptcommand,
        commandslog,
        timelog,
        resultdict,
    ):
        return 1

    os.chdir(curdir)
    print("Test", testName, "passed")
    return logAndReturn(commandslog, timelog, resultdict, 0)


def initializer(tm_path, iree_path):
    global SHARED_TORCH_MLIR_BUILD, SHARED_IREE_BUILD
    SHARED_TORCH_MLIR_BUILD = tm_path
    SHARED_IREE_BUILD = iree_path


def runFrameworkTests(frameworkname, testsList, args, script_dir, run_dir):
    # print(f"In runFrameworkTests - torch mlir build - {TORCH_MLIR_BUILD}")
    poolSize = args.jobs
    print("Running tests for framework", frameworkname, ":", testsList)
    uniqueTestList = []
    [uniqueTestList.append(test) for test in testsList if test not in uniqueTestList]
    if not uniqueTestList:
        return
    tupleOfListArg = []
    # Create list of tuple(test, arg, run_dir) to allow launching tests in parallel
    [
        tupleOfListArg.append((frameworkname, test, args, script_dir, run_dir))
        for test in uniqueTestList
    ]
    if args.verbose:
        print("Following tests will be run:", uniqueTestList)

    with Pool(poolSize, initializer, (TORCH_MLIR_BUILD, IREE_BUILD)) as p:
        result = p.map_async(runTest, tupleOfListArg)
        result.wait()
        if args.verbose:
            print("All tasks submitted to process pool completed")


def checkAndSetEnvironments(args):
    HF_HOME = os.environ.get("HF_HOME")

    if args.hfhome:
        HF_HOME = args.hfhome

    if HF_HOME:
        if not os.path.exists(HF_HOME):
            print(
                "Hugging Face HF_HOME environment variable or --hfhome argument value",
                HF_HOME,
                "does not exist. Set your HF_HOME to a valid dir.",
            )
            return 1
        os.environ["HF_HOME"] = HF_HOME
    else:
        print("Your Hugging Face Home is not set. Use --hfhome or set HF_HOME env.")
        return 1
    print("HF_HOME:", os.environ.get("HF_HOME"))
    return 0


def main():
    global TORCH_MLIR_BUILD, IREE_BUILD
    msg = "The run.py script to run e2e shark tests"
    parser = argparse.ArgumentParser(prog="run.py", description=msg, epilog="")
    parser.add_argument(
        "-b",
        "--backend",
        choices=["llvm-cpu", "amd-aie", "rocm"],
        default="llvm-cpu",
        help="Target backend hardware",
    )
    parser.add_argument(
        "-c",
        "--torchmlirbuild",
        required=True,
        help="Path to the torch-mlir build",
    )
    parser.add_argument(
        "-d",
        "--dtype",
        choices=["fp32", "bf16"],
        default="fp32",
        help="Tensor datatype to use",
    )
    parser.add_argument(
        "-f",
        "--frameworks",
        nargs="*",
        default=["onnx"],
        choices=["pytorch", "onnx", "tensorflow"],
        help="Run tests for given framework(s)",
    )
    parser.add_argument(
        "-g",
        "--groups",
        nargs="*",
        default=["operators", "combinations"],
        choices=["operators", "combinations", "models"],
        help="Run given group of tests",
    )
    parser.add_argument(
        "-i",
        "--ireebuild",
        help="Path to the IREE build",
    )
    parser.add_argument(
        "--hfhome",
        help="Hugging Face Home (HF_HOME) directory, a dir with large free space ",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="Number of parallel processes to use for running tests",
    )
    parser.add_argument(
        "-l",
        "--torchtolinalg",
        action="store_true",
        default=False,
        help="Have torch-mlir-opt to produce linalg instead of torch mlir and pass that to iree-compile",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["direct", "onnx", "ort"],
        default="onnx",
        help="Use framework to torch MLIR, PyTorch to ONNX or ONNX plus ONNX RT stub flow",
    )
    parser.add_argument(
        "-r",
        "--rundirectory",
        default="test-run",
        help="Path to the torch-mlir build",
    )
    parser.add_argument(
        "-t",
        "--tests",
        nargs="*",
        help="Run given specific test(s) only. Other test run options will be ignored.",
    )
    parser.add_argument(
        "-u",
        "--upto",
        choices=["torch-mlir", "ireecompile", "inference"],
        default="torch-mlir",
        help="Stop after genearting torch MLIR, or after IREE compilation, or go all the way to running inference.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Print aditional messsages to show progress",
    )
    parser.add_argument(
        "-z",
        "--zerotolerance",
        action="store_true",
        default=False,
        help="Do not allow any tolerance in comparing results",
    )

    args = parser.parse_args()
    frameworks = args.frameworks

    if args.torchmlirbuild:
        TORCH_MLIR_BUILD = args.torchmlirbuild
    TORCH_MLIR_BUILD = os.path.abspath(TORCH_MLIR_BUILD)
    if not os.path.exists(TORCH_MLIR_BUILD):
        print(
            "Torch MLIR build directory",
            TORCH_MLIR_BUILD,
            "does not exist.",
        )
        sys.exit(1)

    if args.ireebuild:
        IREE_BUILD = args.ireebuild
    if args.upto == "ireecompile" or args.upto == "inference":
        if not IREE_BUILD:
            print(
                "If --upto is 'ireecompile' or 'inference' then a valid IREE build is needed. Specify a valid IREE build directory using --ireebuild or set IREE_BUILD in run.py"
            )
            sys.exit(1)
        IREE_BUILD = os.path.abspath(IREE_BUILD)
        if not os.path.exists(IREE_BUILD):
            print("IREE build directory", IREE_BUILD, "does not exist.")
            sys.exit(1)

    run_dir = os.path.abspath(args.rundirectory)
    # Root dir where run.py is
    script_dir = os.path.dirname(os.path.realpath(__file__))

    if not os.path.exists(run_dir):
        try:
            os.mkdir(run_dir)
        except OSError as errormsg:
            print("Could not make run directory", run_dir, " Error message: ", errormsg)
            sys.exit(1)
    print("Starting e2eshark tests. Using", args.jobs, "processes")
    if args.verbose:
        print("Test run with arguments: ", vars(args))
    print("Torch MLIR build:", TORCH_MLIR_BUILD)
    if IREE_BUILD:
        print("IREE build:", IREE_BUILD)
    print("Test run directory:", run_dir)

    if checkAndSetEnvironments(args):
        sys.exit(1)

    # if args.tests used, that means run given specific tests, the --frameworks options will be
    # ignored in that case
    if args.tests:
        print("Since --tests was specified, --groups tests will not be run")
        testsList = args.tests
        # Strip leading/trailing slashes
        # Construct a dictionary of framework name and list of tests in them
        frameworktotests_dict = {"pytorch": [], "onnx": [], "tensorflow": []}
        for item in testsList:
            if not os.path.exists(item):
                print("Test", item, "does not exist")
                sys.exit(1)
            testName = item.strip(os.sep)
            frameworkname = testName.split("/")[0]
            if frameworkname not in frameworktotests_dict:
                print(
                    "Test name must start with a valid framework name: pytorch, onnx, tensorflow. Invalid name:",
                    frameworkname,
                )
            frameworktotests_dict[frameworkname] += [testName]
        for framework in frameworktotests_dict:
            testsList = frameworktotests_dict[framework]
            runFrameworkTests(framework, testsList, args, script_dir, run_dir)
    else:
        for framework in frameworks:
            testsList = getTestsList(framework, args.groups)
            runFrameworkTests(framework, testsList, args, script_dir, run_dir)

    # When all processes are done, print
    print("Completed run of e2e shark tests")


if __name__ == "__main__":
    main()

"""REAL-model smoke test — NOT part of the mocked pytest suite.

Sends a generated bar-chart image with KNOWN labels (Q2: 72%, Q4: 84%)
through Qwen2.5-VL via llama-cpp-python and requires the model's answer to
contain recognizable values — a nonempty response alone is NOT sufficient.

Guards: refuses to run until BOTH the model and the MATCHING projector
(mmproj-F16.gguf) are present, and reports handler/GPU-capability separately.

Run AFTER the CUDA-enabled install and model downloads (README sections 3-4):

    python scripts/smoke_test_model.py

Exit 0 = image accepted AND recognized values found. Exit 1 = actionable error.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    # 0. Preconditions: model AND matching projector must exist.
    from config import Config
    if not Path(Config.MODEL_PATH).is_file():
        print(f"SKIP/FAIL: model file missing at {Config.MODEL_PATH}")
        return 1
    if not Path(Config.MMPROJ_PATH).is_file():
        print(f"SKIP/FAIL: projector missing at {Config.MMPROJ_PATH}.")
        print("Download mmproj-F16.gguf from the SAME unsloth/"
              "Qwen2.5-VL-7B-Instruct-GGUF revision before running this test.")
        return 1

    # 1. Capability report — no inference claims from these alone.
    try:
        import llama_cpp
        gpu_capable = bool(getattr(llama_cpp, "llama_supports_gpu_offload",
                                   lambda: False)())
        print(f"llama-cpp-python {llama_cpp.__version__}")
        print(f"build supports GPU offload: {gpu_capable}")
        if not gpu_capable:
            print("NOTE: CPU-only build. This test validates vision inference "
                  "but says NOTHING about GPU placement or VRAM fit.")
    except ImportError:
        print("ERROR: llama-cpp-python not installed. See README section 3.")
        return 1
    try:
        from llama_cpp.llama_chat_format import Qwen25VLChatHandler  # noqa
        print("Qwen25VLChatHandler: available")
    except ImportError:
        print("ERROR: Qwen25VLChatHandler missing — upgrade llama-cpp-python.")
        return 1

    # 2. Known test image: labeled bar chart.
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (640, 400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([80, 250, 200, 350], fill="steelblue")
    d.rectangle([300, 150, 420, 350], fill="darkorange")
    d.text((80, 230), "Q2: 72%")
    d.text((300, 130), "Q4: 84%")
    d.text((80, 365), "Production efficiency by quarter")
    img_path = Path("data/smoke_chart.png")
    img_path.parent.mkdir(exist_ok=True)
    img.save(img_path)

    # 3. Real vision inference.
    from services import model_service
    try:
        answer = model_service.analyze_page_image(
            str(img_path),
            "This bar chart has two labeled bars. Read the exact labels "
            "(quarter and percentage) of both bars and state which is higher.")
    except model_service.ModelUnavailableError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"\nModel answer:\n{answer}\n")

    # 4. Structured recognition check — non-empty output is NOT enough:
    #    require Q2 bound to 72(%), Q4 bound to 84(%), and Q4 named higher.
    a = answer.lower()
    def bound(quarter, value):
        return bool(
            re.search(rf"{quarter}[^\n.;]{{0,30}}(?<!\d){value}(?!\d)", a)
            or re.search(rf"(?<!\d){value}(?!\d)\s*%?[^\n.;]{{0,30}}{quarter}", a))
    q2_ok, q4_ok = bound("q2", "72"), bound("q4", "84")
    higher_ok = bool(
        re.search(r"q4[^\n.;]{0,40}(higher|greater|more|larger|increase|improv)", a)
        or re.search(r"(higher|greater|more|larger|increase|improv)[^\n.;]{0,40}q4", a)
        or re.search(r"84\s*%\s*(>|is greater than|exceeds)[^\n.;]{0,20}72", a))
    checks = {"Q2->72": q2_ok, "Q4->84": q4_ok, "Q4 higher": higher_ok}
    print(f"recognition checks: {checks}")
    if not all(checks.values()):
        print("FAIL: the model did not verifiably read the chart labels.")
        print("Check projector pairing (mmproj must match the model revision).")
        return 1

    status = model_service.model_status()
    print(f"loaded: {status['loaded']}")
    print(f"GPU offload supported by build: {status['gpu_offload_supported']}")
    print(f"GPU placement observed: {status['gpu_placement_observed']} "
          "(llama.cpp cannot verify per-layer placement)")
    print(f"visual_inference_tested: {status['visual_inference_tested']}")
    print("\nSMOKE TEST PASSED: image input accepted and chart values recognized.")
    print("Note: this does NOT prove VRAM fit at other settings or general "
          "chart accuracy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

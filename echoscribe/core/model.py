"""FunASR model singleton with GPU OOM auto-fallback to CPU.

Hides ``funasr`` and ``torch`` behind lazy imports so that callers
(tests, CLI ``--help``, web app boot) don't pay the import cost or
require the dependency unless transcription actually runs.

OOM fallback runs at LOAD time only. Runtime OOM (during
``model.generate``) is handled in ``echoscribe.core.transcribe`` since
recovery requires resetting this singleton.
"""
_model = None


def is_model_loaded():
    """Return True if the singleton has been initialized."""
    return _model is not None


def reset_model():
    """Drop the singleton and free CUDA cache. Used by runtime OOM recovery."""
    global _model
    _model = None
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def get_model(status_callback=None, force_device=None):
    """Return the FunASR model singleton, loading on first call.

    Args:
        status_callback: Optional callable receiving ``"loading"`` then
            ``"ready"``. Used by SSE routes to surface load progress.
        force_device: ``"cpu"`` or ``"cuda"`` to override auto-detection.
            Used by ``reset_model`` + reload after runtime OOM.
    """
    global _model
    if _model is not None:
        return _model

    import torch
    from funasr import AutoModel

    device = force_device or ("cuda" if torch.cuda.is_available() else "cpu")
    if status_callback:
        status_callback("loading")
    print(f"正在加载 EchoScribe 模型 (设备: {device})...")

    try:
        _model = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=device,
            disable_update=True,
        )
    except RuntimeError as e:
        if "out of memory" in str(e).lower() and device == "cuda":
            print("[model] CUDA OOM during load, falling back to CPU...")
            _model = AutoModel(
                model="paraformer-zh",
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                device="cpu",
                disable_update=True,
            )
        else:
            raise

    if status_callback:
        status_callback("ready")
    print("EchoScribe 模型加载完成。")
    return _model

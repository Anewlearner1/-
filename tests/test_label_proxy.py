"""labeling/proxy.py: every proxy frame's barcode decodes back to its OpenCV
decoder index, independently re-decoded here with cv2."""
import cv2
import numpy as np
import pytest

from labeling import proxy
from synth import write_jittery_video, write_slow_pan_video, write_steady_textured_video


def _decode_all(path):
    cap = cv2.VideoCapture(str(path))
    reads = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        reads.append(proxy.read_barcode(frame))
    cap.release()
    return reads


def _write_moving_ball_video(path, fps, n, width, height):
    """Smooth gradient court with a moving ball: realistic for VP8, unlike pure noise."""
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    base = np.zeros((height, width, 3), np.uint8)
    base[..., 1] = np.linspace(60, 160, width, dtype=np.uint8)[None, :]
    base[..., 0] = np.linspace(40, 120, height, dtype=np.uint8)[:, None]
    for i in range(n):
        img = base.copy()
        cv2.circle(img, ((i * 23) % width, height // 2 + (i * 7) % (height // 3)), 15,
                   (40, 230, 230), -1)
        wr.write(img)
    wr.release()
    return path


@pytest.mark.parametrize("fps,n,size", [(30.0, 120, (320, 240)), (29.97, 90, (1280, 720)),
                                        (60.0, 61, (720, 1280))])
def test_every_proxy_frame_reads_back_its_decoder_index(tmp_path, fps, n, size):
    if size == (320, 240):
        src = write_slow_pan_video(tmp_path / "src.mp4", fps, n, width=size[0], height=size[1],
                                   shift_per_frame=2.0)
    else:
        src = _write_moving_ball_video(tmp_path / "src.mp4", fps, n, *size)
    info = proxy.make_label_proxy(src, tmp_path / "p.webm")
    out = tmp_path / "p.webm"
    assert out.exists() and out.stat().st_size > 0
    assert info["frame_count"] == n
    assert info["fps"] == pytest.approx(fps, rel=1e-3)
    assert max(info["width"], info["height"] - proxy.BAR_H) <= proxy.MAX_SIDE
    assert proxy.BAR_H >= 24
    reads = _decode_all(out)
    assert reads == list(range(n))
    cap = cv2.VideoCapture(str(out))
    assert cap.get(cv2.CAP_PROP_FRAME_WIDTH) == info["width"]
    assert cap.get(cv2.CAP_PROP_FRAME_HEIGHT) == info["height"]
    cap.release()


def test_barcode_survives_noisy_content_and_large_numbers(tmp_path):
    """High-entropy picture content next to the strip, and frame numbers with
    many 1 bits, still read exactly after VP8."""
    w, h = 320, 32 + 64
    frames = [1, 2, 4095, 65535, 699050, (1 << 20) - 1]
    path = tmp_path / "n.webm"
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"VP80"), 30.0, (w, h))
    rng = np.random.default_rng(0)
    for f in frames:
        img = rng.integers(0, 255, (h, w, 3)).astype(np.uint8)
        proxy.draw_barcode(img[:proxy.BAR_H], f)
        wr.write(img)
    wr.release()
    assert _decode_all(path) == frames


def test_reader_rejects_non_barcode_and_corrupted_check_bits():
    assert proxy.read_barcode(np.full((40, 280, 3), 128, np.uint8)) is None
    img = np.zeros((40, 280, 3), np.uint8)
    proxy.draw_barcode(img[:proxy.BAR_H], 1234)
    assert proxy.read_barcode(img) == 1234
    cell = 280 / proxy.N_CELLS
    x = int(cell * 23)                              # a check-bit cell
    img[:proxy.BAR_H, x:int(x + cell)] = 255 - img[5, x + 2]
    assert proxy.read_barcode(img) is None


def test_cache_reuses_proxy_and_stays_under_gitignored_data(tmp_path, monkeypatch):
    monkeypatch.setenv(proxy.PROXY_DIR_ENV, str(tmp_path / "proxies"))
    src = write_jittery_video(tmp_path / "a.b.mp4", 30.0, 10)
    p1, info1 = proxy.cached_proxy(src)
    mtime = p1.stat().st_mtime_ns
    p2, info2 = proxy.cached_proxy(src)
    assert p1 == p2 and info1 == info2 and p2.stat().st_mtime_ns == mtime
    assert p1.name.startswith("a.b-") and p1.suffix == ".webm"
    assert proxy.DEFAULT_PROXY_DIR.parts[-2:] == ("data", "label_proxies")
    gitignore = (proxy.DEFAULT_PROXY_DIR.parent.parent / ".gitignore").read_text()
    assert "data/" in gitignore.split()


def test_falls_back_to_frames_mode_when_vp8_is_unavailable(tmp_path, monkeypatch):
    from labeling import proxy
    real = proxy.make_label_proxy

    def no_vp8(video, out, fourcc="VP80"):
        if fourcc == "VP80":
            raise proxy.EncoderUnavailable("VP80 encoder unavailable")
        return real(video, out, fourcc)

    monkeypatch.setenv("RALLY_LABEL_PROXY_DIR", str(tmp_path / "px"))
    monkeypatch.setattr(proxy, "make_label_proxy", no_vp8)
    src = write_steady_textured_video(tmp_path / "c.mp4", 30.0, 25)
    path, info = proxy.cached_proxy(src)
    assert info["mode"] == "frames" and path.suffix == ".avi" and info["frame_count"] == 25
    for n in (0, 13, 24, 7):                                # random access, verified by barcode
        assert proxy.read_barcode(proxy.read_proxy_frame(path, n)) == n
    again, info2 = proxy.cached_proxy(src)                  # cache reused, same mode
    assert again == path and info2["mode"] == "frames"

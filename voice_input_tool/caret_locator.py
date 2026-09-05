"""入力欄（キャレット）の画面上の位置を Accessibility API で取得する。

プレビューパネルを「いま文字を打っている場所の近く」に出すための基準矩形を返す。
戦略は caret → element → window → mouse の順に試し、どれかで必ず InputAnchor を返す。

実測メモ（PyObjC 12.2 / macOS 26 で確認済み）:
- `AXUIElementCopyAttributeValue(elem, attr, None)` は `(err, value)` のタプルを返す。
- `AXUIElementCopyParameterizedAttributeValue(elem, attr, param, None)` も `(err, value)`。
- `AXValueGetValue(axvalue, type, None)` は `(ok, value)` のタプル。型が合わないと `(False, None)`。
- AX の座標系は「メイン画面の左上が原点・下向きが正」。Cocoa は「メイン画面の左下が原点・上向きが正」。
- Chrome はテキスト以外の要素（AXButton）でも kAXBoundsForRange が err=0 で返り、
  値は (0, 画面高さ, 0, 0) のようなゴミになる。err だけ見ずに矩形の妥当性を検証する必要がある。
- TextEdit（ルーラー表示時）はキャレット矩形が実際より 1 行分（18pt）上にずれて返る。
  フォーカス要素の枠より少しだけ外れているときは枠内へ寄せる。
"""

import logging
import os
from typing import NamedTuple, Optional

log = logging.getLogger("voice_input")

try:
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXUIElementCopyAttributeValue,
        AXUIElementCopyParameterizedAttributeValue,
        AXUIElementCreateApplication,
        AXUIElementSetMessagingTimeout,
        AXValueGetType,
        AXValueGetValue,
        kAXBoundsForRangeParameterizedAttribute,
        kAXErrorSuccess,
        kAXFocusedUIElementAttribute,
        kAXFocusedWindowAttribute,
        kAXPositionAttribute,
        kAXSelectedTextRangeAttribute,
        kAXSizeAttribute,
        kAXValueCGPointType,
        kAXValueCGRectType,
        kAXValueCGSizeType,
        kAXWindowsAttribute,
    )

    HAS_AX = True
except ImportError:
    HAS_AX = False

try:
    from AppKit import NSEvent, NSScreen, NSWorkspace

    HAS_APPKIT = True
except ImportError:
    HAS_APPKIT = False


# 対象アプリが固まっていてもホットキー処理を長く止めないための AX 応答待ち上限（秒）。
# 既定は約 6 秒で、録音開始が体感で遅れてしまう
AX_MESSAGING_TIMEOUT = 1.0

# キャレット矩形の高さの許容範囲（pt）。0 以下はゴミ、極端に大きいのも行ではないので捨てる
CARET_MIN_HEIGHT = 1.0
CARET_MAX_HEIGHT = 200.0

# キャレットがフォーカス要素の枠からこの距離までなら「少しずれているだけ」とみなし枠内へ寄せる。
# TextEdit の 1 行分（18pt）のずれを吸収し、Chrome の (0, 1080) のような遠いゴミは弾く
CARET_SNAP_TOLERANCE = 24.0

# element/window の矩形がこの高さを超えると（Chrome の AXWebArea やターミナルの AXTextArea は
# ウィンドウ大になる）「その直下」は画面外や無関係な場所になるので、入力位置としては使えない。
# その場合はマウス位置か、矩形の下辺中央付近の小さな矩形に落とす
LARGE_RECT_HEIGHT = 200.0
# 大きな矩形を下辺中央の小さな矩形に落とすときの寸法（pt）。
# x はプレビューパネル幅（460pt）の半分ぶん左に寄せて、パネルが横中央に来るようにする
LARGE_RECT_FALLBACK_HALF_PANEL_WIDTH = 230.0
LARGE_RECT_FALLBACK_LINE_HEIGHT = 18.0
LARGE_RECT_FALLBACK_BOTTOM_MARGIN = 24.0


class InputAnchor(NamedTuple):
    x: float  # Cocoa スクリーン座標（左下原点）。パネルを置く基準矩形
    y: float
    width: float
    height: float
    kind: str  # "caret" | "element" | "window" | "mouse"


class _AXRect(NamedTuple):
    """AX 座標系（左上原点）の矩形。変換前の中間表現"""

    x: float
    y: float
    width: float
    height: float


def locate_input_anchor(pid: Optional[int]) -> InputAnchor:
    """対象アプリ（pid）の入力位置を返す。例外を外に出さない。必ず何か返す。"""
    try:
        return _locate(pid)
    except Exception as e:
        log.warning("入力位置の取得で予期しないエラー: %s", e)
        return _mouse_anchor()


def _locate(pid: Optional[int]) -> InputAnchor:
    target_pid = _resolve_pid(pid)
    if target_pid is None:
        log.info("入力位置: 対象アプリを特定できないためマウス位置を使用")
        return _mouse_anchor()

    if not HAS_AX or not _accessibility_trusted():
        log.info("入力位置: アクセシビリティ未許可のためマウス位置を使用 (pid=%s)", target_pid)
        return _mouse_anchor()

    app_ref = AXUIElementCreateApplication(target_pid)
    if app_ref is None:
        return _mouse_anchor()
    try:
        AXUIElementSetMessagingTimeout(app_ref, AX_MESSAGING_TIMEOUT)
    except Exception:
        pass

    focused = _copy_attr(app_ref, kAXFocusedUIElementAttribute)
    element_rect = _element_frame(focused) if focused is not None else None

    caret_rect = _caret_rect(focused, element_rect) if focused is not None else None
    if caret_rect is not None:
        anchor = _to_cocoa(caret_rect, "caret")
        log.info("入力位置: caret pid=%s rect=%s", target_pid, anchor)
        return anchor

    if element_rect is not None:
        anchor = _shrink_large_anchor(_to_cocoa(element_rect, "element"))
        log.info("入力位置: element pid=%s rect=%s", target_pid, anchor)
        return anchor

    window_rect = _window_frame(app_ref)
    if window_rect is not None:
        anchor = _shrink_large_anchor(_to_cocoa(window_rect, "window"))
        log.info("入力位置: window pid=%s rect=%s", target_pid, anchor)
        return anchor

    anchor = _mouse_anchor()
    log.info("入力位置: mouse pid=%s rect=%s", target_pid, anchor)
    return anchor


def _resolve_pid(pid: Optional[int]) -> Optional[int]:
    """pid が無ければ前面アプリを使う。自プロセスは対象にしない（AX で見ても入力欄は無い）"""
    own_pid = os.getpid()
    if pid:
        return None if int(pid) == own_pid else int(pid)
    if not HAS_APPKIT:
        return None
    try:
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception as e:
        log.warning("前面アプリ取得エラー: %s", e)
        return None
    if front is None:
        return None
    front_pid = int(front.processIdentifier())
    if front_pid == own_pid:
        return None
    return front_pid


def _accessibility_trusted() -> bool:
    try:
        return bool(AXIsProcessTrusted())
    except Exception as e:
        log.warning("アクセシビリティ権限確認エラー: %s", e)
        return False


def _copy_attr(element, attribute):
    """属性値を取る。失敗時は None（err はログに残さない。無い属性は普通にある）"""
    try:
        err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception as e:
        log.debug("AX 属性取得エラー %s: %s", attribute, e)
        return None
    if err != kAXErrorSuccess:
        return None
    return value


def _unpack_axvalue(value, value_type):
    """AXValue から中身を取り出す。型が合わない・AXValue でない場合は None"""
    if value is None:
        return None
    try:
        if AXValueGetType(value) != value_type:
            return None
        ok, inner = AXValueGetValue(value, value_type, None)
    except Exception:
        # kAXSelectedTextRange 等に AXValue 以外を返すアプリがあるため握りつぶす
        return None
    if not ok:
        return None
    return inner


def _element_frame(element) -> Optional[_AXRect]:
    position = _unpack_axvalue(_copy_attr(element, kAXPositionAttribute), kAXValueCGPointType)
    size = _unpack_axvalue(_copy_attr(element, kAXSizeAttribute), kAXValueCGSizeType)
    if position is None or size is None:
        return None
    rect = _AXRect(float(position.x), float(position.y), float(size.width), float(size.height))
    if rect.width <= 0 or rect.height <= 0:
        return None
    return rect


def _caret_rect(focused, element_rect: Optional[_AXRect]) -> Optional[_AXRect]:
    """選択範囲（幅 0 ならキャレット）の矩形。妥当性を検証して通ったものだけ返す"""
    selected_range = _copy_attr(focused, kAXSelectedTextRangeAttribute)
    if selected_range is None:
        return None
    try:
        err, bounds = AXUIElementCopyParameterizedAttributeValue(
            focused, kAXBoundsForRangeParameterizedAttribute, selected_range, None
        )
    except Exception as e:
        log.debug("kAXBoundsForRange 取得エラー: %s", e)
        return None
    if err != kAXErrorSuccess or bounds is None:
        return None
    cg_rect = _unpack_axvalue(bounds, kAXValueCGRectType)
    if cg_rect is None:
        return None
    rect = _AXRect(
        float(cg_rect.origin.x),
        float(cg_rect.origin.y),
        max(0.0, float(cg_rect.size.width)),
        float(cg_rect.size.height),
    )
    return _validate_caret(rect, element_rect)


def _validate_caret(rect: _AXRect, element_rect: Optional[_AXRect]) -> Optional[_AXRect]:
    """err=0 でもゴミが返るアプリ（Chrome）があるため、行の高さと要素枠との位置関係で判定する"""
    if not (CARET_MIN_HEIGHT <= rect.height <= CARET_MAX_HEIGHT):
        return None
    if element_rect is None:
        # 要素枠が無ければ照合できない。高さが行らしい値なら信用する
        return rect

    tol = CARET_SNAP_TOLERANCE
    left, top = element_rect.x, element_rect.y
    right, bottom = left + element_rect.width, top + element_rect.height
    if (
        rect.x < left - tol
        or rect.x > right + tol
        or rect.y < top - tol
        or rect.y + rect.height > bottom + tol
    ):
        log.debug("キャレット矩形 %s が要素枠 %s から外れているため不採用", rect, element_rect)
        return None

    # 少しだけ外れているときは枠内へ寄せる（TextEdit のルーラー分のずれ対策）
    x = min(max(rect.x, left), right)
    y = min(max(rect.y, top), max(top, bottom - rect.height))
    if (x, y) != (rect.x, rect.y):
        log.debug("キャレット矩形を要素枠内へ補正: (%s, %s) -> (%s, %s)", rect.x, rect.y, x, y)
    return _AXRect(x, y, rect.width, rect.height)


def _window_frame(app_ref) -> Optional[_AXRect]:
    window = _copy_attr(app_ref, kAXFocusedWindowAttribute)
    if window is None:
        windows = _copy_attr(app_ref, kAXWindowsAttribute)
        # Notion のように err=0 で None が返るケースがある
        if not windows:
            return None
        window = windows[0]
    return _element_frame(window)


def _primary_screen_height() -> float:
    """AX→Cocoa の変換にはメイン画面（screens()[0]）の高さを使う。
    対象ディスプレイの高さを使うとマルチモニタで座標が崩れる"""
    if not HAS_APPKIT:
        return 0.0
    try:
        screens = NSScreen.screens()
        if screens:
            return float(screens[0].frame().size.height)
    except Exception as e:
        log.warning("スクリーン情報の取得エラー: %s", e)
    return 0.0


def _to_cocoa(rect: _AXRect, kind: str) -> InputAnchor:
    """AX 座標（左上原点）→ Cocoa 座標（左下原点）。y は矩形の下端になる"""
    primary_height = _primary_screen_height()
    return InputAnchor(
        x=rect.x,
        y=primary_height - (rect.y + rect.height),
        width=rect.width,
        height=rect.height,
        kind=kind,
    )


def _shrink_large_anchor(anchor: InputAnchor) -> InputAnchor:
    """ウィンドウ大の element/window 矩形を、パネルを置ける小さな矩形に落とす。

    マウスが矩形の中にあれば「そこで操作している」とみなしてマウス位置を使う。
    そうでなければ矩形の下辺中央付近（ターミナルのプロンプトやチャットの入力欄は
    たいてい下にある）。kind は window に揃える。
    """
    if anchor.height <= LARGE_RECT_HEIGHT:
        return anchor
    mouse = _mouse_anchor()
    if (
        anchor.x <= mouse.x <= anchor.x + anchor.width
        and anchor.y <= mouse.y <= anchor.y + anchor.height
    ):
        log.debug("矩形 %s が大きすぎるためマウス位置 %s を使用", anchor, mouse)
        return mouse
    center_x = anchor.x + anchor.width / 2.0
    fallback = InputAnchor(
        x=max(anchor.x, center_x - LARGE_RECT_FALLBACK_HALF_PANEL_WIDTH),
        y=anchor.y + LARGE_RECT_FALLBACK_BOTTOM_MARGIN,
        width=1.0,
        height=LARGE_RECT_FALLBACK_LINE_HEIGHT,
        kind="window",
    )
    log.debug("矩形 %s が大きすぎるため下辺中央 %s を使用", anchor, fallback)
    return fallback


def _mouse_anchor() -> InputAnchor:
    """最後の砦。マウス位置は Cocoa 座標で取れるので変換不要"""
    if HAS_APPKIT:
        try:
            point = NSEvent.mouseLocation()
            return InputAnchor(float(point.x), float(point.y), 1.0, 1.0, "mouse")
        except Exception as e:
            log.warning("マウス位置の取得エラー: %s", e)
    return InputAnchor(0.0, 0.0, 1.0, 1.0, "mouse")

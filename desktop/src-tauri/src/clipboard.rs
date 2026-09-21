//! Native Windows Clipboard Copy payloads and adapter.
//!
//! Pure payload construction lives here so it stays deterministic and
//! unit-testable on any platform. Only [`WindowsClipboardWriter`] touches the
//! Win32 clipboard, and every caller must finish validation, managed-path
//! resolution, file reads, decoding, and allocation before invoking it. That
//! ordering keeps preflight failures from emptying a user's clipboard.

use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::sync::{Condvar, Mutex};

use crate::sidecar::SidecarError;

/// Maximum decoded pixels accepted for a static Clipboard Copy.
///
/// Matches the import pipeline's per-frame pixel budget so a Library Copy that
/// imported successfully can also be copied without unbounded allocation.
const MAX_CLIPBOARD_PIXELS: u64 = 64_000_000;
const MAX_CLIPBOARD_DIMENSION: u32 = 16_384;
const BITMAPV5HEADER_SIZE: u32 = 124;
const BI_BITFIELDS: u32 = 3;
const LCS_SRGB: u32 = 0x7352_4742;
const LCS_GM_IMAGES: u32 = 4;
const DROPFILES_HEADER_SIZE: u32 = 20;

/// How a managed Library Copy must be published on the clipboard.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ManagedCopyKind {
    StaticImage,
    GifFile,
}

/// A decoded static image ready for the clipboard.
///
/// Width and height are the decoded dimensions; tests assert the clipboard
/// payload preserves them exactly, and future success metadata may report them.
pub(crate) struct StaticImagePayload {
    pub dibv5: Vec<u8>,
    pub png: Vec<u8>,
    #[allow(dead_code)]
    pub width: u32,
    #[allow(dead_code)]
    pub height: u32,
}

/// A fully allocated clipboard payload. Callers build this before opening the
/// OS clipboard so failures never mutate clipboard state.
pub(crate) enum ClipboardPayload {
    StaticImage(StaticImagePayload),
    FileDrop { hdrop: Vec<u8> },
}

/// Win32 clipboard access behind a trait so tests can substitute a fake.
pub(crate) trait ClipboardWriter {
    fn write_static_image(&self, dibv5: &[u8], png: &[u8]) -> Result<(), SidecarError>;
    fn write_file_drop(&self, hdrop: &[u8]) -> Result<(), SidecarError>;
}

// All three native copy commands share this gate. It covers preflight and
// the OS write together, so requests publish in the order they enter this
// native boundary. The gate lives here, beside the writer abstraction, rather
// than in a UI owner: inspector, wall, and batch callers all share one queue.
// ponytail: one FIFO gate; add per-window queues only if clipboard throughput
// becomes a measured requirement.
struct ClipboardWriteState {
    next_ticket: u64,
    serving_ticket: u64,
}

struct ClipboardWriteGate {
    state: Mutex<ClipboardWriteState>,
    turn: Condvar,
}

struct ClipboardWriteTurn<'a> {
    gate: &'a ClipboardWriteGate,
    ticket: u64,
}

impl ClipboardWriteGate {
    const fn new() -> Self {
        Self {
            state: Mutex::new(ClipboardWriteState {
                next_ticket: 0,
                serving_ticket: 0,
            }),
            turn: Condvar::new(),
        }
    }

    fn acquire(&self) -> ClipboardWriteTurn<'_> {
        let ticket = {
            let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
            let ticket = state.next_ticket;
            state.next_ticket = state
                .next_ticket
                .checked_add(1)
                .expect("clipboard write ticket counter exhausted");
            ticket
        };
        // This notification is useful to waiters and makes ticket assignment
        // observable to the deterministic concurrency tests below. Notify
        // after releasing the state lock so a waiter cannot miss the change.
        self.turn.notify_all();
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        while state.serving_ticket != ticket {
            state = self
                .turn
                .wait(state)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
        }
        drop(state);
        ClipboardWriteTurn {
            gate: self,
            ticket,
        }
    }

    fn with<T>(&self, operation: impl FnOnce() -> T) -> T {
        let _turn = self.acquire();
        operation()
    }
}

impl Drop for ClipboardWriteTurn<'_> {
    fn drop(&mut self) {
        let mut state = self
            .gate
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        debug_assert_eq!(state.serving_ticket, self.ticket);
        state.serving_ticket = self
            .ticket
            .checked_add(1)
            .expect("clipboard write ticket counter exhausted");
        self.gate.turn.notify_all();
    }
}

static CLIPBOARD_WRITE_GATE: ClipboardWriteGate = ClipboardWriteGate::new();

pub(crate) fn with_clipboard_write<T>(operation: impl FnOnce() -> T) -> T {
    CLIPBOARD_WRITE_GATE.with(operation)
}

pub(crate) fn write_payload_via(
    writer: &impl ClipboardWriter,
    payload: &ClipboardPayload,
) -> Result<(), SidecarError> {
    match payload {
        ClipboardPayload::StaticImage(image) => {
            writer.write_static_image(&image.dibv5, &image.png)
        }
        ClipboardPayload::FileDrop { hdrop } => writer.write_file_drop(hdrop),
    }
}

/// Classify a resolved managed Library Copy by extension.
///
/// Static stills decode to `CF_DIBV5` plus a registered PNG payload. GIFs keep
/// their encoded animation via a `CF_HDROP` file reference and are never
/// decoded here.
pub(crate) fn classify_managed_path(path: &Path) -> Result<ManagedCopyKind, SidecarError> {
    let extension = path
        .extension()
        .and_then(|extension| extension.to_str())
        .map(|extension| extension.to_ascii_lowercase())
        .unwrap_or_default();
    match extension.as_str() {
        "png" | "jpg" | "jpeg" | "webp" | "bmp" => Ok(ManagedCopyKind::StaticImage),
        "gif" => Ok(ManagedCopyKind::GifFile),
        _ => Err(SidecarError::new(
            "Unsupported Clipboard Copy media type.",
        )),
    }
}

/// Build a `CF_DIBV5` payload from top-down RGBA pixels.
///
/// The header is a 124-byte `BITMAPV5HEADER` with `BI_BITFIELDS` 32-bit BGRA
/// masks followed by bottom-up pixel rows. Width and height are preserved
/// exactly; no scaling, cropping, or rotation is applied.
pub(crate) fn build_dibv5(
    width: u32,
    height: u32,
    rgba: &[u8],
) -> Result<Vec<u8>, SidecarError> {
    if width == 0 || height == 0 {
        return Err(SidecarError::new(
            "Clipboard Copy image dimensions must be positive.",
        ));
    }
    if width > MAX_CLIPBOARD_DIMENSION || height > MAX_CLIPBOARD_DIMENSION {
        return Err(SidecarError::new(
            "Clipboard Copy image exceeds the supported dimensions.",
        ));
    }
    let pixel_count = u64::from(width) * u64::from(height);
    if pixel_count > MAX_CLIPBOARD_PIXELS {
        return Err(SidecarError::new(
            "Clipboard Copy image exceeds the supported pixel budget.",
        ));
    }
    let expected = pixel_count as usize * 4;
    if rgba.len() != expected {
        return Err(SidecarError::new(
            "Clipboard Copy pixel buffer does not match its dimensions.",
        ));
    }

    let stride = width as usize * 4;
    let mut dibv5 = Vec::with_capacity(124 + expected);
    dibv5.extend_from_slice(&BITMAPV5HEADER_SIZE.to_le_bytes());
    dibv5.extend_from_slice(&(width as i32).to_le_bytes());
    dibv5.extend_from_slice(&(height as i32).to_le_bytes());
    dibv5.extend_from_slice(&1_u16.to_le_bytes());
    dibv5.extend_from_slice(&32_u16.to_le_bytes());
    dibv5.extend_from_slice(&BI_BITFIELDS.to_le_bytes());
    dibv5.extend_from_slice(&(expected as u32).to_le_bytes());
    dibv5.extend_from_slice(&0_i32.to_le_bytes());
    dibv5.extend_from_slice(&0_i32.to_le_bytes());
    dibv5.extend_from_slice(&0_u32.to_le_bytes());
    dibv5.extend_from_slice(&0_u32.to_le_bytes());
    dibv5.extend_from_slice(&0x00FF_0000_u32.to_le_bytes());
    dibv5.extend_from_slice(&0x0000_FF00_u32.to_le_bytes());
    dibv5.extend_from_slice(&0x0000_00FF_u32.to_le_bytes());
    dibv5.extend_from_slice(&0xFF00_0000_u32.to_le_bytes());
    dibv5.extend_from_slice(&LCS_SRGB.to_le_bytes());
    dibv5.extend_from_slice(&[0_u8; 36]);
    dibv5.extend_from_slice(&0_u32.to_le_bytes());
    dibv5.extend_from_slice(&0_u32.to_le_bytes());
    dibv5.extend_from_slice(&0_u32.to_le_bytes());
    dibv5.extend_from_slice(&LCS_GM_IMAGES.to_le_bytes());
    dibv5.extend_from_slice(&0_u32.to_le_bytes());
    dibv5.extend_from_slice(&0_u32.to_le_bytes());
    dibv5.extend_from_slice(&0_u32.to_le_bytes());
    debug_assert_eq!(dibv5.len(), 124);

    for row in (0..height as usize).rev() {
        let offset = row * stride;
        let scanline = &rgba[offset..offset + stride];
        for pixel in scanline.as_chunks::<4>().0 {
            dibv5.push(pixel[2]);
            dibv5.push(pixel[1]);
            dibv5.push(pixel[0]);
            dibv5.push(pixel[3]);
        }
    }
    Ok(dibv5)
}

/// Decode still-image bytes and build both clipboard payloads.
///
/// Returns `CF_DIBV5` bytes plus an encoded PNG payload. GIF support is
/// deliberately not compiled in, so encoded GIF bytes can never be flattened
/// through this path; GIF Library Copies must use a file reference instead.
pub(crate) fn build_static_image_payload(
    file_bytes: &[u8],
) -> Result<StaticImagePayload, SidecarError> {
    if file_bytes.is_empty() {
        return Err(SidecarError::new(
            "Clipboard Copy source image is empty.",
        ));
    }
    let image = image::load_from_memory(file_bytes)
        .map_err(|_| SidecarError::new("Clipboard Copy source image could not be decoded."))?;
    let rgba = image.to_rgba8();
    let (width, height) = (rgba.width(), rgba.height());
    let dibv5 = build_dibv5(width, height, rgba.as_raw())?;

    let mut png = Vec::new();
    image
        .write_to(&mut Cursor::new(&mut png), image::ImageFormat::Png)
        .map_err(|_| SidecarError::new("Clipboard Copy PNG payload could not be encoded."))?;
    if png.is_empty() {
        return Err(SidecarError::new(
            "Clipboard Copy PNG payload could not be encoded.",
        ));
    }
    Ok(StaticImagePayload {
        dibv5,
        png,
        width,
        height,
    })
}

/// Encode one path for a `CF_HDROP` payload. Windows paths are UTF-16 natively
/// and need not be valid Unicode, so the Windows build encodes the `OsStr`
/// directly instead of round-tripping through `str`.
#[cfg(windows)]
fn encode_hdrop_path(path: &Path) -> Result<Vec<u16>, SidecarError> {
    use std::os::windows::ffi::OsStrExt;
    let wide: Vec<u16> = path.as_os_str().encode_wide().collect();
    if wide.is_empty() || wide.contains(&0) {
        return Err(SidecarError::new(
            "Clipboard Copy file paths must be encodable file references.",
        ));
    }
    Ok(wide)
}

#[cfg(not(windows))]
fn encode_hdrop_path(path: &Path) -> Result<Vec<u16>, SidecarError> {
    let text = path.to_str().ok_or_else(|| {
        SidecarError::new("Clipboard Copy file paths must be encodable file references.")
    })?;
    if text.is_empty() || text.contains('\0') {
        return Err(SidecarError::new(
            "Clipboard Copy file paths must be encodable file references.",
        ));
    }
    Ok(text.encode_utf16().collect())
}

/// Build a `CF_HDROP` payload for resolved Library Copy paths.
///
/// The result is a `DROPFILES` header (`pFiles = 20`, Unicode) followed by
/// NUL-terminated UTF-16 paths and a final NUL. Callers resolve and validate
/// every path before calling; this function only encodes.
pub(crate) fn build_hdrop_payload(paths: &[PathBuf]) -> Result<Vec<u8>, SidecarError> {
    if paths.is_empty() {
        return Err(SidecarError::new(
            "Clipboard Copy requires at least one file.",
        ));
    }
    let mut encoded_paths: Vec<Vec<u16>> = Vec::with_capacity(paths.len());
    let mut payload_len = DROPFILES_HEADER_SIZE as usize + 2;
    for path in paths {
        let wide = encode_hdrop_path(path)?;
        payload_len += (wide.len() + 1) * 2;
        encoded_paths.push(wide);
    }

    let mut payload = Vec::with_capacity(payload_len);
    payload.extend_from_slice(&DROPFILES_HEADER_SIZE.to_le_bytes());
    payload.extend_from_slice(&0_i32.to_le_bytes());
    payload.extend_from_slice(&0_i32.to_le_bytes());
    payload.extend_from_slice(&0_u32.to_le_bytes());
    payload.extend_from_slice(&1_u32.to_le_bytes());
    for wide in &encoded_paths {
        for unit in wide {
            payload.extend_from_slice(&unit.to_le_bytes());
        }
        payload.extend_from_slice(&0_u16.to_le_bytes());
    }
    payload.extend_from_slice(&0_u16.to_le_bytes());
    Ok(payload)
}

/// Decode a `CF_HDROP` payload back to paths. Test-only helper that mirrors
/// the encoder so round-trips stay honest.
#[cfg(test)]
pub(crate) fn parse_hdrop_payload(payload: &[u8]) -> Result<Vec<PathBuf>, SidecarError> {
    if payload.len() < 20 {
        return Err(SidecarError::new("Clipboard Copy file payload is truncated."));
    }
    let p_files = u32::from_le_bytes(payload[0..4].try_into().expect("header slice"));
    let wide_flag = u32::from_le_bytes(payload[16..20].try_into().expect("header slice"));
    if p_files != DROPFILES_HEADER_SIZE || wide_flag != 1 {
        return Err(SidecarError::new(
            "Clipboard Copy file payload has an unexpected header.",
        ));
    }
    let body = &payload[p_files as usize..];
    if !body.len().is_multiple_of(2) {
        return Err(SidecarError::new(
            "Clipboard Copy file payload is truncated.",
        ));
    }
    let units: Vec<u16> = body
        .as_chunks::<2>()
        .0
        .iter()
        .map(|chunk| u16::from_le_bytes(*chunk))
        .collect();
    let mut paths = Vec::new();
    let mut current = Vec::new();
    for unit in units {
        if unit == 0 {
            if current.is_empty() {
                break;
            }
            let text = String::from_utf16(&current).map_err(|_| {
                SidecarError::new("Clipboard Copy file payload has an unexpected header.")
            })?;
            paths.push(PathBuf::from(text));
            current.clear();
        } else {
            current.push(unit);
        }
    }
    if !current.is_empty() || paths.is_empty() {
        return Err(SidecarError::new(
            "Clipboard Copy file payload is truncated.",
        ));
    }
    Ok(paths)
}

/// The real Win32 clipboard. Owns every OS call; all payload bytes arrive
/// fully allocated so preflight stays outside the clipboard open window.
#[cfg(windows)]
pub(crate) struct WindowsClipboardWriter;

/// Stable Win32 clipboard format identifiers. windows-sys does not export the
/// `CF_*` constants, so the documented values are pinned here.
#[cfg(windows)]
const CF_DIBV5: u32 = 17;
#[cfg(windows)]
const CF_HDROP: u32 = 15;

#[cfg(windows)]
impl ClipboardWriter for WindowsClipboardWriter {
    fn write_static_image(&self, dibv5: &[u8], png: &[u8]) -> Result<(), SidecarError> {
        let png_format = unsafe {
            let name: Vec<u16> = "PNG\0".encode_utf16().collect();
            windows_sys::Win32::System::DataExchange::RegisterClipboardFormatW(name.as_ptr())
        };
        if png_format == 0 {
            return Err(SidecarError::new(
                "Clipboard Copy could not register the PNG clipboard format.",
            ));
        }
        write_clipboard_formats(&[(CF_DIBV5, dibv5), (png_format, png)])
    }

    fn write_file_drop(&self, hdrop: &[u8]) -> Result<(), SidecarError> {
        write_clipboard_formats(&[(CF_HDROP, hdrop)])
    }
}

#[cfg(windows)]
fn write_clipboard_formats(formats: &[(u32, &[u8])]) -> Result<(), SidecarError> {
    use windows_sys::Win32::Foundation::{GetLastError, GlobalFree};
    use windows_sys::Win32::System::DataExchange::{
        CloseClipboard, EmptyClipboard, OpenClipboard, SetClipboardData,
    };
    use windows_sys::Win32::System::Memory::{GlobalAlloc, GlobalLock, GlobalUnlock, GMEM_MOVEABLE};

    for format in formats {
        if format.1.is_empty() {
            return Err(SidecarError::new("Clipboard Copy payload is empty."));
        }
    }

    unsafe {
        let mut opened = false;
        for _ in 0..5 {
            if OpenClipboard(std::ptr::null_mut()) != 0 {
                opened = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        if !opened {
            return Err(SidecarError::new(format!(
                "Clipboard Copy could not open the Windows clipboard (error {}).",
                GetLastError()
            )));
        }
        let close = |result: Result<(), SidecarError>| {
            CloseClipboard();
            result
        };

        if EmptyClipboard() == 0 {
            let code = GetLastError();
            return close(Err(SidecarError::new(format!(
                "Clipboard Copy could not empty the Windows clipboard (error {code})."
            ))));
        }

        for (format, bytes) in formats {
            let handle = GlobalAlloc(GMEM_MOVEABLE, bytes.len());
            if handle.is_null() {
                let code = GetLastError();
                return close(Err(SidecarError::new(format!(
                    "Clipboard Copy could not allocate clipboard memory (error {code})."
                ))));
            }
            let locked = GlobalLock(handle);
            if locked.is_null() {
                GlobalFree(handle);
                let code = GetLastError();
                return close(Err(SidecarError::new(format!(
                    "Clipboard Copy could not lock clipboard memory (error {code})."
                ))));
            }
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), locked as *mut u8, bytes.len());
            GlobalUnlock(handle);
            if SetClipboardData(*format, handle).is_null() {
                GlobalFree(handle);
                let code = GetLastError();
                return close(Err(SidecarError::new(format!(
                    "Clipboard Copy could not publish clipboard data (error {code})."
                ))));
            }
        }
        close(Ok(()))
    }
}

/// Non-Windows builds compile but refuse at runtime; Clipboard Copy is a
/// Windows-first contract and the sidecar only ships on Windows.
#[cfg(not(windows))]
pub(crate) struct WindowsClipboardWriter;

#[cfg(not(windows))]
impl ClipboardWriter for WindowsClipboardWriter {
    fn write_static_image(&self, _dibv5: &[u8], _png: &[u8]) -> Result<(), SidecarError> {
        Err(SidecarError::new("Clipboard Copy requires Windows."))
    }

    fn write_file_drop(&self, _hdrop: &[u8]) -> Result<(), SidecarError> {
        Err(SidecarError::new("Clipboard Copy requires Windows."))
    }
}

/// Records clipboard writes without touching the OS. Every preflight test
/// asserts against this: failures must leave it empty.
#[cfg(test)]
pub(crate) struct FakeClipboardWriter {
    pub writes: std::sync::Mutex<Vec<FakeClipboardWrite>>,
}

#[cfg(test)]
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum FakeClipboardWrite {
    StaticImage { dibv5: Vec<u8>, png: Vec<u8> },
    FileDrop { hdrop: Vec<u8> },
}

#[cfg(test)]
impl FakeClipboardWriter {
    pub(crate) fn new() -> Self {
        Self {
            writes: std::sync::Mutex::new(Vec::new()),
        }
    }

    pub(crate) fn write_count(&self) -> usize {
        self.writes.lock().expect("fake clipboard lock").len()
    }
}

#[cfg(test)]
impl ClipboardWriter for FakeClipboardWriter {
    fn write_static_image(&self, dibv5: &[u8], png: &[u8]) -> Result<(), SidecarError> {
        if dibv5.is_empty() || png.is_empty() {
            return Err(SidecarError::new("Clipboard Copy payload is empty."));
        }
        self.writes.lock().expect("fake clipboard lock").push(
            FakeClipboardWrite::StaticImage {
                dibv5: dibv5.to_vec(),
                png: png.to_vec(),
            },
        );
        Ok(())
    }

    fn write_file_drop(&self, hdrop: &[u8]) -> Result<(), SidecarError> {
        if hdrop.is_empty() {
            return Err(SidecarError::new("Clipboard Copy payload is empty."));
        }
        self.writes.lock().expect("fake clipboard lock").push(
            FakeClipboardWrite::FileDrop {
                hdrop: hdrop.to_vec(),
            },
        );
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{ImageBuffer, Rgba};
    use std::panic::{catch_unwind, AssertUnwindSafe};
    use std::sync::{mpsc, Arc};
    use std::thread;

    #[derive(Debug, PartialEq, Eq)]
    enum ClipboardEvent {
        Started(&'static str),
        Wrote(&'static str),
        Completed(&'static str),
    }

    #[derive(Clone)]
    struct OrderedClipboardWriter {
        label: &'static str,
        events: Arc<Mutex<Vec<ClipboardEvent>>>,
    }

    impl OrderedClipboardWriter {
        fn record(&self, event: ClipboardEvent) {
            self.events
                .lock()
                .expect("clipboard event lock")
                .push(event);
        }
    }

    impl ClipboardWriter for OrderedClipboardWriter {
        fn write_static_image(
            &self,
            _dibv5: &[u8],
            _png: &[u8],
        ) -> Result<(), SidecarError> {
            self.record(ClipboardEvent::Wrote(self.label));
            Ok(())
        }

        fn write_file_drop(&self, _hdrop: &[u8]) -> Result<(), SidecarError> {
            self.record(ClipboardEvent::Wrote(self.label));
            Ok(())
        }
    }

    fn wait_for_ticket(gate: &ClipboardWriteGate, ticket_count: u64) {
        let mut state = gate
            .state
            .lock()
            .expect("clipboard gate state should not be poisoned");
        while state.next_ticket < ticket_count {
            state = gate
                .turn
                .wait(state)
                .expect("clipboard gate wait should not be poisoned");
        }
    }

    fn test_image_payload() -> ClipboardPayload {
        ClipboardPayload::StaticImage(StaticImagePayload {
            dibv5: vec![1],
            png: vec![2],
            width: 1,
            height: 1,
        })
    }

    fn test_file_payload() -> ClipboardPayload {
        ClipboardPayload::FileDrop { hdrop: vec![1] }
    }

    fn solid_rgba(width: u32, height: u32, pixel: [u8; 4]) -> Vec<u8> {
        let frame: ImageBuffer<Rgba<u8>, Vec<u8>> =
            ImageBuffer::from_pixel(width, height, Rgba(pixel));
        frame.into_raw()
    }

    fn encode_png(width: u32, height: u32, pixel: [u8; 4]) -> Vec<u8> {
        let frame: ImageBuffer<Rgba<u8>, Vec<u8>> =
            ImageBuffer::from_pixel(width, height, Rgba(pixel));
        let mut bytes = Vec::new();
        image::DynamicImage::ImageRgba8(frame)
            .write_to(&mut Cursor::new(&mut bytes), image::ImageFormat::Png)
            .expect("png fixture should encode");
        bytes
    }

    fn encode_bmp(width: u32, height: u32, pixel: [u8; 4]) -> Vec<u8> {
        let frame: ImageBuffer<Rgba<u8>, Vec<u8>> =
            ImageBuffer::from_pixel(width, height, Rgba(pixel));
        let mut bytes = Vec::new();
        image::DynamicImage::ImageRgba8(frame)
            .write_to(&mut Cursor::new(&mut bytes), image::ImageFormat::Bmp)
            .expect("bmp fixture should encode");
        bytes
    }

    fn encode_with(width: u32, height: u32, pixel: [u8; 4], format: image::ImageFormat) -> Vec<u8> {
        let frame: ImageBuffer<Rgba<u8>, Vec<u8>> =
            ImageBuffer::from_pixel(width, height, Rgba(pixel));
        let mut bytes = Vec::new();
        image::DynamicImage::ImageRgba8(frame)
            .write_to(&mut Cursor::new(&mut bytes), format)
            .unwrap_or_else(|_| panic!("{format:?} fixture should encode"));
        bytes
    }

    #[test]
    fn classifies_still_and_gif_library_copies_by_extension() {
        for name in [
            "asset.png",
            "asset.PNG",
            "asset.jpg",
            "asset.jpeg",
            "asset.webp",
            "asset.bmp",
            "asset.BMP",
        ] {
            assert_eq!(
                classify_managed_path(Path::new(name)).expect("still should classify"),
                ManagedCopyKind::StaticImage,
                "{name} should be a static image"
            );
        }
        assert_eq!(
            classify_managed_path(Path::new("asset.gif")).expect("gif should classify"),
            ManagedCopyKind::GifFile
        );
        assert_eq!(
            classify_managed_path(Path::new("asset.GIF")).expect("gif should classify"),
            ManagedCopyKind::GifFile
        );
        assert!(classify_managed_path(Path::new("asset.tiff")).is_err());
        assert!(classify_managed_path(Path::new("asset")).is_err());
        assert!(classify_managed_path(Path::new("asset.sqlite")).is_err());
    }

    #[test]
    fn builds_a_valid_dibv5_without_changing_dimensions() {
        let rgba = solid_rgba(3, 2, [10, 20, 30, 255]);
        let dibv5 = build_dibv5(3, 2, &rgba).expect("dibv5 should build");

        assert_eq!(dibv5.len(), 124 + 3 * 2 * 4);
        assert_eq!(u32::from_le_bytes(dibv5[0..4].try_into().unwrap()), 124);
        assert_eq!(i32::from_le_bytes(dibv5[4..8].try_into().unwrap()), 3);
        assert_eq!(i32::from_le_bytes(dibv5[8..12].try_into().unwrap()), 2);
        assert_eq!(u16::from_le_bytes(dibv5[12..14].try_into().unwrap()), 1);
        assert_eq!(u16::from_le_bytes(dibv5[14..16].try_into().unwrap()), 32);
        assert_eq!(u32::from_le_bytes(dibv5[16..20].try_into().unwrap()), 3);
        assert_eq!(
            u32::from_le_bytes(dibv5[20..24].try_into().unwrap()),
            3 * 2 * 4
        );
        assert_eq!(
            u32::from_le_bytes(dibv5[40..44].try_into().unwrap()),
            0x00FF_0000
        );
        assert_eq!(
            u32::from_le_bytes(dibv5[52..56].try_into().unwrap()),
            0xFF00_0000
        );
        // First stored row is the bottom source row, converted RGBA -> BGRA.
        assert_eq!(&dibv5[124..128], &[30, 20, 10, 255]);
    }

    #[test]
    fn rejects_invalid_dibv5_inputs_before_allocation() {
        assert!(build_dibv5(0, 2, &[]).is_err());
        assert!(build_dibv5(2, 0, &[]).is_err());
        assert!(build_dibv5(2, 2, &[0_u8; 15]).is_err());
        assert!(build_dibv5(2, 2, &[0_u8; 17]).is_err());
        assert!(build_dibv5(20_000, 2, &[]).is_err());
    }

    #[test]
    fn builds_static_payloads_for_png_and_bmp_fixtures() {
        for (label, bytes) in [
            ("png", encode_png(4, 3, [200, 30, 40, 255])),
            ("bmp", encode_bmp(4, 3, [10, 220, 60, 255])),
            (
                "jpeg",
                encode_with(4, 3, [10, 220, 60, 255], image::ImageFormat::Jpeg),
            ),
            (
                "webp",
                encode_with(4, 3, [10, 220, 60, 255], image::ImageFormat::WebP),
            ),
        ] {
            let payload = build_static_image_payload(&bytes)
                .unwrap_or_else(|_| panic!("{label} fixture should decode"));
            assert_eq!((payload.width, payload.height), (4, 3), "{label} size");
            assert_eq!(payload.dibv5.len(), 124 + 4 * 3 * 4, "{label} dibv5");
            assert_eq!(
                &payload.png[0..8],
                &[137, 80, 78, 71, 13, 10, 26, 10],
                "{label} png signature"
            );
            let decoded = image::load_from_memory(&payload.png).expect("png round-trip");
            assert_eq!((decoded.width(), decoded.height()), (4, 3));
        }
    }

    #[test]
    fn rejects_empty_and_corrupt_static_sources() {
        assert!(build_static_image_payload(&[]).is_err());
        assert!(build_static_image_payload(b"not an image").is_err());
        assert!(build_static_image_payload(&[137, 80, 78, 71]).is_err());
    }

    #[test]
    fn never_flattens_gif_bytes_through_the_static_path() {
        // Minimal 1x1 GIF89a payload. The static builder has no GIF decoder,
        // so even raw GIF bytes fail closed instead of publishing frame one.
        let gif = vec![
            0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
            0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0x21, 0xF9, 0x04, 0x01, 0x00, 0x00, 0x00,
            0x00, 0x2C, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x02, 0x02,
            0x44, 0x01, 0x00, 0x3B,
        ];
        assert!(build_static_image_payload(&gif).is_err());
    }

    #[test]
    fn builds_single_and_multi_hdrop_payloads() {
        let single = vec![PathBuf::from(r"C:\MemeSort\originals\asset.gif")];
        let payload = build_hdrop_payload(&single).expect("single hdrop should build");
        assert_eq!(parse_hdrop_payload(&payload).expect("round-trip"), single);

        let multi = vec![
            PathBuf::from(r"C:\MemeSort\originals\first.png"),
            PathBuf::from(r"C:\MemeSort\originals\second.png"),
        ];
        let payload = build_hdrop_payload(&multi).expect("multi hdrop should build");
        assert_eq!(parse_hdrop_payload(&payload).expect("round-trip"), multi);
        // One header plus two NUL-terminated entries plus the final NUL.
        assert!(payload.len() > 20);
        assert_eq!(&payload[payload.len() - 4..], &[0, 0, 0, 0]);
    }

    #[test]
    fn rejects_empty_hdrop_batches() {
        assert!(build_hdrop_payload(&[]).is_err());
    }

    #[test]
    fn records_static_and_file_writes_through_the_fake_adapter() {
        let fake = FakeClipboardWriter::new();
        let rgba = solid_rgba(2, 2, [1, 2, 3, 255]);
        let dibv5 = build_dibv5(2, 2, &rgba).expect("dibv5");
        let png = encode_png(2, 2, [1, 2, 3, 255]);

        fake.write_static_image(&dibv5, &png).expect("static write");
        let hdrop = build_hdrop_payload(&[PathBuf::from(r"C:\MemeSort\originals\a.png")])
            .expect("hdrop");
        fake.write_file_drop(&hdrop).expect("file write");

        assert_eq!(fake.write_count(), 2);
        let writes = fake.writes.lock().expect("lock");
        assert!(matches!(
            &writes[0],
            FakeClipboardWrite::StaticImage { .. }
        ));
        assert!(matches!(&writes[1], FakeClipboardWrite::FileDrop { .. }));
    }

    #[test]
    fn serializes_image_wall_and_batch_writes_in_fifo_ticket_and_completion_order() {
        let gate = Arc::new(ClipboardWriteGate::new());
        let events = Arc::new(Mutex::new(Vec::<ClipboardEvent>::new()));
        let (first_started_tx, first_started_rx) = mpsc::channel();
        let (release_first_tx, release_first_rx) = mpsc::channel();

        let first_events = Arc::clone(&events);
        let first_gate = Arc::clone(&gate);
        let first = thread::spawn(move || {
            let writer = OrderedClipboardWriter {
                label: "inspector/image A",
                events: first_events,
            };
            first_gate.with(|| {
                writer.record(ClipboardEvent::Started(writer.label));
                first_started_tx
                    .send(())
                    .expect("first request should signal readiness");
                release_first_rx
                    .recv()
                    .expect("first request should be released");
                write_payload_via(&writer, &test_image_payload())
                    .expect("image write should succeed");
                writer.record(ClipboardEvent::Completed(writer.label));
            });
        });
        first_started_rx
            .recv()
            .expect("first request should acquire the clipboard gate");

        let second_events = Arc::clone(&events);
        let second_gate = Arc::clone(&gate);
        let second = thread::spawn(move || {
            let writer = OrderedClipboardWriter {
                label: "wall/original B",
                events: second_events,
            };
            second_gate.with(|| {
                writer.record(ClipboardEvent::Started(writer.label));
                write_payload_via(&writer, &test_file_payload())
                    .expect("wall original write should succeed");
                writer.record(ClipboardEvent::Completed(writer.label));
            });
        });
        wait_for_ticket(&gate, 2);

        let third_events = Arc::clone(&events);
        let third_gate = Arc::clone(&gate);
        let third = thread::spawn(move || {
            let writer = OrderedClipboardWriter {
                label: "batch/originals A+B",
                events: third_events,
            };
            third_gate.with(|| {
                writer.record(ClipboardEvent::Started(writer.label));
                write_payload_via(&writer, &test_file_payload())
                    .expect("batch original write should succeed");
                writer.record(ClipboardEvent::Completed(writer.label));
            });
        });
        wait_for_ticket(&gate, 3);

        assert_eq!(
            events.lock().expect("clipboard event lock").as_slice(),
            &[ClipboardEvent::Started("inspector/image A")]
        );
        release_first_tx
            .send(())
            .expect("first request should be released");

        first.join().expect("first copy should finish");
        second.join().expect("second copy should finish");
        third.join().expect("third copy should finish");
        let events = events.lock().expect("clipboard event lock").clone();
        assert_eq!(
            events,
            vec![
                ClipboardEvent::Started("inspector/image A"),
                ClipboardEvent::Wrote("inspector/image A"),
                ClipboardEvent::Completed("inspector/image A"),
                ClipboardEvent::Started("wall/original B"),
                ClipboardEvent::Wrote("wall/original B"),
                ClipboardEvent::Completed("wall/original B"),
                ClipboardEvent::Started("batch/originals A+B"),
                ClipboardEvent::Wrote("batch/originals A+B"),
                ClipboardEvent::Completed("batch/originals A+B"),
            ]
        );
        let state = gate.state.lock().expect("clipboard gate state");
        assert_eq!(state.next_ticket, 3);
        assert_eq!(state.serving_ticket, 3);
    }

    #[test]
    fn advances_fifo_gate_after_error_and_panic() {
        let gate = ClipboardWriteGate::new();
        let error = gate.with(|| Err::<(), _>("native write failed"));
        assert_eq!(error, Err("native write failed"));

        let panic_result = catch_unwind(AssertUnwindSafe(|| {
            gate.with(|| -> () { panic!("native write panicked") });
        }));
        assert!(panic_result.is_err());
        assert_eq!(gate.with(|| Ok::<_, &'static str>(())), Ok(()));
        let state = gate.state.lock().expect("clipboard gate state");
        assert_eq!(state.next_ticket, 3);
        assert_eq!(state.serving_ticket, 3);
    }
}

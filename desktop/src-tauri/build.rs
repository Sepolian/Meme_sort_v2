fn main() {
    // Tauri embeds `frontendDist` in the executable.  The portable builder runs
    // Vite first, so Cargo must invalidate this build when those files change.
    println!("cargo:rerun-if-changed=../dist");
    tauri_build::build()
}

export function isMacOS(): boolean {
  return navigator.platform.startsWith("Mac");
}

export function fileManagerName(): string {
  return isMacOS() ? "Finder" : "File Explorer";
}

export function revealLabel(): string {
  return isMacOS() ? "Reveal in Finder" : "Reveal in Explorer";
}

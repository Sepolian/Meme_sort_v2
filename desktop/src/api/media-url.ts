import { convertFileSrc, isTauri } from "@tauri-apps/api/core";

export function mediaUrl(projectedPath: string | null): string | undefined {
  if (!projectedPath?.startsWith("/media/")) {
    return undefined;
  }
  // Convert only the origin: encoding the route would hide its /media/ segments.
  const origin = isTauri() ? convertFileSrc("", "memesort-media") : "http://memesort-media.localhost/";
  return `${origin}${projectedPath.slice(1)}`;
}

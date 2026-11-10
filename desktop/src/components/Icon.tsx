const paths = {
  library: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  duplicates: "M8 8h12v13H8z M16 5V3H3v14h2",
  sliders: "M3 7h5m4 0h9M3 17h10m4 0h4 M8 4v6m5 4v6",
  keyboard: "M2 5h20v14H2z M5 9h1m3 0h1m3 0h1m3 0h1 M7 14h10",
  search: "M16 16l5 5 M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0",
  image: "M3 3h18v18H3z M3 17l6-6 5 5 3-3 4 4 M16 7h.01",
  plus: "M12 4v16 M4 12h16",
};

export function Icon({ name }: { name: keyof typeof paths }) {
  return <svg className="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

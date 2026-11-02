import type { CSSProperties } from 'react'

const paths: Record<string, string> = {
  overview: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10',
  chat: 'M4 5h16v12H9l-5 4V5 M8 9h8 M8 13h5',
  tasks: 'M9 5h11v4H9z M9 15h11v4H9z M3 7h2 M3 17h2',
  approvals: 'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6',
  memory: 'M5 3h14v18H5z M9 7h6 M9 11h6 M9 15h4',
  tools: 'M14 4a5 5 0 0 0-6 6l-5 7 4 4 7-7a5 5 0 0 0 6-6l-4 4-4-4z',
  capabilities: 'M12 3l9 9-9 9-9-9z M12 7v10 M7 12h10',
  mcp: 'M3 8h6v8H3z M15 8h6v8h-6z M9 12h6 M6 4v4 M18 16v4',
  workflows: 'M3 4h6v6H3z M15 14h6v6h-6z M6 10v7h9 M9 7h9v7',
  schedules: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l4 2',
  activity: 'M3 13h4l3-8 4 14 3-6h4',
  browser: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M3 12h18 M12 3c-5 6-5 12 0 18 M12 3c5 6 5 12 0 18',
  computer: 'M3 4h18v12H3z M12 16v4 M7 20h10',
  voice: 'M9 4a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0z M5 10v1a7 7 0 0 0 14 0v-1 M12 18v3 M8 21h8',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
  rotate: 'M3 10a9 9 0 1 1 2 8 M3 4v6h6', pause: 'M8 5v14 M16 5v14', play: 'M8 4l12 8-12 8z', reset: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
  arrow: 'M5 12h14 M14 7l5 5-5 5', close: 'M6 6l12 12 M6 18L18 6',
}
export default function Icon({ name, style }: { name: string; style?: CSSProperties }) {
  return <svg className="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}><path d={paths[name] ?? paths.overview} /></svg>
}

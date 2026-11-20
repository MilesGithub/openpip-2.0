import { BADGE_STYLE, SYNTHETIC_BADGE } from './badgeWords'

export function SyntheticBadge() {
  // No props: whether it shows is the caller's, through F3's isSyntheticShown, never `is_synthetic` alone,
  // which counts only the releases the caller may see while citations go to every caller (J21).
  return <span style={BADGE_STYLE}>{SYNTHETIC_BADGE}</span>
}

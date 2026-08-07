window.getYieldClass = value => {
  if (value == null || Number.isNaN(Number(value))) return '';
  if (Number(value) >= 0.08) return 'yield-tier-high';
  if (Number(value) >= 0.06) return 'yield-tier-watch';
  return '';
};

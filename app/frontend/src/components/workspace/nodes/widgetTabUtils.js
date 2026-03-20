export function resolveWidgetDefaultTab(tabs, preferredTab, fallbackTab) {
  const available = (tabs || []).map((tab) => tab.id);
  if (preferredTab && available.includes(preferredTab)) {
    return preferredTab;
  }
  if (fallbackTab && available.includes(fallbackTab)) {
    return fallbackTab;
  }
  return available[0] || fallbackTab || 'content';
}

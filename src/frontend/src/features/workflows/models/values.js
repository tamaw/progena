

export function cloneValue(value) {
  return value ? JSON.parse(JSON.stringify(value)) : value;
}

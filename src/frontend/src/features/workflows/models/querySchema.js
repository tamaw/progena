import { cloneValue } from './values';

export function buildQueryInputSchema(filters, documentSchema) {
  if (!filters || filters.length === 0) {
    return { type: 'object', required: ['trigger'], properties: { trigger: {} } };
  }
  const occurrences = filters.reduce((counts, filter) => ({
    ...counts,
    [filter.field]: (counts[filter.field] || 0) + 1,
  }), {});
  const properties = {};
  filters.forEach((filter) => {
    const fieldSchema = documentSchema?.properties?.[filter.field] || {};
    const port = occurrences[filter.field] === 1 ? filter.field : `${filter.field}_${filter.op}`;
    if (filter.op === 'in') properties[port] = { type: 'array', items: cloneValue(fieldSchema) };
    else if (filter.op === 'exists') properties[port] = { type: 'boolean' };
    else if (filter.op === 'contains') properties[port] = { anyOf: [{ type: 'string' }, cloneValue(fieldSchema.items) || {}] };
    else properties[port] = cloneValue(fieldSchema);
  });
  return { type: 'object', properties };
}

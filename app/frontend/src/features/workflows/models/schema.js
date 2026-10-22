import { cloneValue } from './values';

export function schemaProperties(schema) {
  if (!schema || typeof schema !== 'object') {
    return [];
  }
  const variants = Array.isArray(schema.oneOf) ? schema.oneOf : [schema];
  const properties = new Map();
  variants.forEach((variant) => {
    if (variant?.type !== 'object' || !variant.properties || typeof variant.properties !== 'object') {
      return;
    }
    const required = new Set(Array.isArray(variant.required) ? variant.required : []);
    Object.entries(variant.properties).forEach(([name, definition]) => {
      const existing = properties.get(name);
      properties.set(name, {
        name,
        definition: definition && typeof definition === 'object' ? definition : {},
        required: Boolean(existing?.required || required.has(name)),
      });
    });
  });
  return [...properties.values()];
}

export function schemaType(definition) {
  if (typeof definition?.title === 'string' && definition.title) return definition.title;
  if (Array.isArray(definition?.type)) return definition.type.join(' | ');
  if (typeof definition?.type === 'string') return definition.type;
  if (Array.isArray(definition?.enum)) return definition.enum.join(' | ');
  return 'any';
}

// Mirrors app/progenitor/workflows.py: workflow_node_output_ports /
// workflow_node_input_ports. A data edge connects one existing output port
// to one existing input port; it never mutates either schema.
export function nodeOutputPorts(kind, outputSchema) {
  if (kind === 'data') {
    return [{ name: 'value', definition: outputSchema && typeof outputSchema === 'object' ? outputSchema : {}, required: false }];
  }
  return schemaProperties(outputSchema);
}

export function nodeInputPorts(kind, inputSchema) {
  if (kind === 'data' || kind === 'event' || kind === 'cron') {
    return [];
  }
  return schemaProperties(inputSchema);
}

// Edges attached to ports a node no longer has after its schemas changed
// (e.g. a routine node pointed at a different routine).
export function edgesToRemovedPorts(edges, node) {
  if (!node) return [];
  const kind = node.data?.kind || node.type;
  const inputs = new Set(nodeInputPorts(kind, node.data?.inputSchema).map((port) => port.name));
  const outputs = new Set(nodeOutputPorts(kind, node.data?.outputSchema).map((port) => port.name));
  const port = (handle) => String(handle || '').replace(/^(in|out):/, '');
  return edges.filter((edge) => (
    (edge.target === node.id && !inputs.has(port(edge.targetHandle)))
    || (edge.source === node.id && !outputs.has(port(edge.sourceHandle)))
  ));
}

function schemaTypes(definition) {
  if (Array.isArray(definition?.type)) return definition.type;
  return typeof definition?.type === 'string' ? [definition.type] : [];
}

// Structural compatibility: every type the source can produce is accepted by
// the target, and matching title if both declare one. Mirrors
// _schema_types_compatible() in app/progenitor/workflows.py.
export function schemasAreCompatible(sourceDefinition, targetDefinition) {
  const sourceTypes = schemaTypes(sourceDefinition);
  const targetTypes = new Set(schemaTypes(targetDefinition));
  if (targetTypes.has('number')) targetTypes.add('integer');
  if (sourceTypes.length && targetTypes.size && !sourceTypes.every((type) => targetTypes.has(type))) {
    return false;
  }
  const sourceTitle = sourceDefinition?.title;
  const targetTitle = targetDefinition?.title;
  return !(sourceTitle && targetTitle && sourceTitle !== targetTitle);
}

export function portFromHandle(handle) {
  return handle?.includes(':') ? handle.slice(handle.indexOf(':') + 1) : handle;
}

export function isUnpackableSchema(definition) {
  return schemaTypes(definition).includes('object') && definition?.properties && Object.keys(definition.properties).length > 0;
}

export function hoistDefinitions(target, source) {
  const sourceDefs = source?.$defs || source?.definitions;
  if (!sourceDefs || typeof sourceDefs !== 'object') return;
  const targetDefs = target.$defs || target.definitions;
  const merged = { ...targetDefs, ...cloneValue(sourceDefs) };
  if (source.$defs !== undefined || (target.$defs === undefined && target.definitions === undefined)) {
    target.$defs = merged;
  } else {
    target.definitions = merged;
  }
}

export function resolveLocalSchemaRef(schema, rootSchema) {
  const seen = new Set();
  let resolved = schema;
  while (resolved && typeof resolved === 'object' && typeof resolved.$ref === 'string') {
    const ref = resolved.$ref;
    const [, container, name] = ref.match(/^#\/([^/]+)\/([^/]+)$/) || [];
    const target = (container === '$defs' || container === 'definitions')
      ? rootSchema?.[container]?.[name]
      : null;
    if (seen.has(ref) || !target || typeof target !== 'object') return resolved;
    seen.add(ref);
    resolved = target;
  }
  return resolved;
}

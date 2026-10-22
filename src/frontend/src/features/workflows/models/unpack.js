import { cloneValue } from './values';
import { nodeOutputPorts, portFromHandle, hoistDefinitions, resolveLocalSchemaRef } from './schema';
import { DEFAULT_NODE_SIZES, visibleNodePortProperties } from './nodePalette';

export function deriveUnpackSchemas(nodeId, nodes, edges) {
  const valueEdge = edges.find((e) => e.target === nodeId && portFromHandle(e.targetHandle) === 'value');
  if (!valueEdge) {
    return {
      inputSchema: { type: 'object', required: ['value'], properties: { value: { type: 'object' } } },
      outputSchema: { type: 'object', properties: {} },
    };
  }
  const sourceNode = nodes.find((n) => n.id === valueEdge.source);
  const sourcePort = portFromHandle(valueEdge.sourceHandle);
  const sourceSchema = sourceNode?.data?.outputSchema;
  const sourceProperty = nodeOutputPorts(sourceNode?.data?.kind, sourceSchema)
    .find((p) => p.name === sourcePort);

  const sourceDef = sourceProperty?.definition || { type: 'object' };
  const resolvedSourceDef = resolveLocalSchemaRef(sourceDef, sourceSchema);
  const inputSchema = {
    type: 'object',
    required: ['value'],
    properties: { value: cloneValue(sourceDef) },
  };
  hoistDefinitions(inputSchema, sourceSchema);

  const outputSchema = {
    type: 'object',
    properties: cloneValue(resolvedSourceDef?.properties || {}),
    required: cloneValue(resolvedSourceDef?.required || []),
  };
  hoistDefinitions(outputSchema, sourceDef);
  hoistDefinitions(outputSchema, sourceSchema);

  return { inputSchema, outputSchema };
}

export function recomputeUnpackSchemas(nodes, edges) {
  const unpackIds = nodes.filter((n) => n.data?.kind === 'unpack').map((n) => n.id);
  if (!unpackIds.length) return nodes;

  const nodesById = Object.fromEntries(nodes.map((n) => [n.id, n]));
  // Each pass settles one more link of an Unpack chain; a cycle (rejected on
  // save) must not spin forever, so the pass count is bounded like the server.
  for (let pass = 0; pass <= unpackIds.length; pass += 1) {
    let changed = false;
    for (const unpackId of unpackIds) {
      const unpack = nodesById[unpackId];
      const { inputSchema, outputSchema } = deriveUnpackSchemas(unpackId, Object.values(nodesById), edges);
      const currentInput = JSON.stringify(unpack.data?.inputSchema);
      const currentOutput = JSON.stringify(unpack.data?.outputSchema);
      const nextInput = JSON.stringify(inputSchema);
      const nextOutput = JSON.stringify(outputSchema);
      if (currentInput !== nextInput || currentOutput !== nextOutput) {
        nodesById[unpackId] = { ...unpack, data: { ...unpack.data, inputSchema, outputSchema } };
        changed = true;
      }
    }
    if (!changed) break;
  }
  return sizeUnpackNodes(Object.values(nodesById), edges);
}

// Until the user resizes it, an Unpack node is exactly tall enough for its
// shown ports plus the show/hide-fields toggle row.
export function sizeUnpackNodes(nodes, edges) {
  return nodes.map((node) => {
    if (node.data?.kind !== 'unpack' || node.data?.dataSize || node.data?.expanded) return node;
    const connected = edges.filter((edge) => edge.source === node.id).map((edge) => portFromHandle(edge.sourceHandle));
    const shown = visibleNodePortProperties(nodeOutputPorts('unpack', node.data?.outputSchema), node.height, connected, Boolean(node.data?.showAllFields)).length;
    const height = Math.max(DEFAULT_NODE_SIZES.unpack.data.height, 69 + shown * 28);
    return node.height === height ? node : { ...node, height, style: { ...node.style, height } };
  });
}

export function computeLostEdgesForUnpackChange(currentEdges, nextNodes) {
  const nextNodesById = Object.fromEntries(nextNodes.map((n) => [n.id, n]));
  const lostEdges = [];
  for (const edge of currentEdges) {
    const sourceNode = nextNodesById[edge.source];
    if (sourceNode?.data?.kind !== 'unpack') continue;
    const portName = portFromHandle(edge.sourceHandle);
    const outputProps = sourceNode.data?.outputSchema?.properties || {};
    if (!outputProps[portName]) {
      lostEdges.push(edge);
    }
  }
  return lostEdges;
}

export function pruneLostUnpackEdges(nodes, edges) {
  let nextEdges = edges;
  let nextNodes = recomputeUnpackSchemas(nodes, nextEdges);
  const lostEdges = [];
  while (true) {
    const newlyLost = computeLostEdgesForUnpackChange(nextEdges, nextNodes);
    if (newlyLost.length === 0) break;
    lostEdges.push(...newlyLost);
    const lostIds = new Set(newlyLost.map((edge) => edge.id));
    nextEdges = nextEdges.filter((edge) => !lostIds.has(edge.id));
    nextNodes = recomputeUnpackSchemas(nodes, nextEdges);
  }
  return { nodes: nextNodes, edges: nextEdges, lostEdges };
}

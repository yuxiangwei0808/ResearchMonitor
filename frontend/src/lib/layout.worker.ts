import ELK from 'elkjs/lib/elk.bundled.js'

const elk = new ELK()

self.onmessage = async (event: MessageEvent<{ nodes: string[]; edges: Array<{ id: string; source: string; target: string }> }>) => {
  try {
    const graph = await elk.layout({
      id: 'root',
      layoutOptions: {
        'elk.algorithm': 'layered',
        'elk.direction': 'RIGHT',
        'elk.spacing.nodeNode': '55',
        'elk.layered.spacing.nodeNodeBetweenLayers': '95',
        'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
      },
      children: event.data.nodes.map((id) => ({ id, width: 235, height: 88 })),
      edges: event.data.edges.map((edge) => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })),
    })
    self.postMessage({
      ok: true,
      positions: Object.fromEntries((graph.children ?? []).map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }])),
    })
  } catch (error) {
    self.postMessage({ ok: false, message: error instanceof Error ? error.message : 'ELK layout failed' })
  }
}

export {}

import type { Map, StyleSpecification, LayerSpecification, SkySpecification } from 'maplibre-gl'

export const STYLE_URL = 'https://tiles.openfreemap.org/styles/liberty'

/** Space sky: black beyond the horizon, a light-blue atmosphere rim, fog that fades in only near the ground. */
const SKY_SPACE: SkySpecification = {
  'sky-color': '#02030A',
  'horizon-color': '#7FB4FF',
  'fog-color': '#B9CDF0',
  'fog-ground-blend': 0.9,
  'horizon-fog-blend': 0.45,
  'sky-horizon-blend': 0.85,
  'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 8, 1, 11, 0],
}

const BLUE_MARBLE = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg'
const ESRI_IMAGERY = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

/**
 * Paint overrides for the vector layers drawn on top of satellite imagery.
 * Fills are hidden (the imagery shows land, water and parks), roads become thin white lines,
 * labels get a dark halo, and 3D buildings are light so they read like a model on a photo.
 */
function satellitePaint(layer: LayerSpecification): { paint?: Record<string, unknown>; hide?: boolean } {
  const id = layer.id
  switch (layer.type) {
    case 'background':
      return { paint: { 'background-color': '#02030A' } }
    case 'fill':
      return { hide: true }
    case 'line': {
      if (/casing|tunnel|waterway|park_outline|track|path|bridge_.*casing/.test(id)) return { hide: true }
      const boundary = /boundary|admin/.test(id)
      const major = /motorway|trunk|primary/.test(id)
      return { paint: { 'line-color': boundary ? '#FFFFFF' : '#FFFFFF', 'line-opacity': boundary ? 0.55 : major ? 0.75 : 0.45 } }
    }
    case 'symbol':
      return { paint: { 'text-color': '#FFFFFF', 'text-halo-color': '#0B1220', 'text-halo-width': 1.4, 'text-halo-blur': 0.4, 'icon-opacity': 0.9 } }
    case 'fill-extrusion':
      return { paint: { 'fill-extrusion-color': '#E9EEF8', 'fill-extrusion-opacity': 0.94, 'fill-extrusion-vertical-gradient': true } }
    default:
      return {}
  }
}

/** Fetches the OpenFreeMap Liberty style and turns it into: colour planet (NASA Blue Marble) → satellite (Esri) → roads/labels/3D. */
export async function loadSatelliteStyle(globe = true): Promise<StyleSpecification> {
  const r = await fetch(STYLE_URL)
  const style = (await r.json()) as StyleSpecification
  style.layers = style.layers.filter((l) => l.type !== 'raster')
  for (const src of Object.keys(style.sources)) if (style.sources[src].type === 'raster') delete style.sources[src]
  style.sources.bluemarble = { type: 'raster', tiles: [BLUE_MARBLE], tileSize: 256, maxzoom: 8, attribution: 'NASA GIBS Blue Marble' }
  style.sources.esri = { type: 'raster', tiles: [ESRI_IMAGERY], tileSize: 256, maxzoom: 19, attribution: 'Esri, Maxar, Earthstar Geographics' }
  const rasters: LayerSpecification[] = [
    { id: 'bluemarble', type: 'raster', source: 'bluemarble', paint: { 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 0, 1, 5.5, 1, 7.5, 0], 'raster-fade-duration': 0, 'raster-saturation': 0.05 } },
    { id: 'esri', type: 'raster', source: 'esri', minzoom: 4, paint: { 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 4, 0, 6.5, 1], 'raster-saturation': -0.08, 'raster-brightness-max': 0.96, 'raster-fade-duration': 150 } },
  ]
  const bg = style.layers.findIndex((l) => l.type === 'background')
  style.layers.splice(bg + 1, 0, ...rasters)
  style.layers = style.layers.filter((layer) => {
    const rule = satellitePaint(layer)
    if (rule.hide) return false
    if (rule.paint) {
      const target = layer as LayerSpecification & { paint?: Record<string, unknown> }
      target.paint = { ...(target.paint ?? {}), ...rule.paint }
    }
    return true
  })
  ;(style as StyleSpecification & { projection?: unknown }).projection = { type: globe ? 'globe' : 'mercator' }
  style.sky = globe ? SKY_SPACE : { ...SKY_SPACE, 'sky-color': '#9EC5FF', 'horizon-color': '#DCE9FF', 'fog-color': '#DCE9FF', 'atmosphere-blend': 0 }
  return style
}

/** Runtime hot-swap helper (kept for debugging from the console). */
export function applySatelliteTheme(map: Map) {
  const style = map.getStyle()
  if (!style?.layers) return
  for (const layer of style.layers) {
    const rule = satellitePaint(layer)
    if (rule.hide) { try { map.setLayoutProperty(layer.id, 'visibility', 'none') } catch { /* ignore */ } ; continue }
    for (const [k, v] of Object.entries(rule.paint ?? {})) {
      try { map.setPaintProperty(layer.id, k as never, v as never) } catch { /* ignore */ }
    }
  }
  map.setSky(SKY_SPACE)
}

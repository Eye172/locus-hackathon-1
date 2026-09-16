import { useEffect, useRef, useState } from 'react'
import { Map as MLMap, Popup, type MapLayerMouseEvent, type ExpressionSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { Route, Locate } from 'lucide-react'
import { loadSatelliteStyle } from '../map/darkTheme'
import type { Campus, ContextPack, Photo, University } from '../lib/types'

const KIND_COLOR: Record<string, string> = { dormitory: '#7C3AED', library: '#0284C7', sports: '#15803D', academic: '#1D4ED8', student_life: '#D97706', other: '#CBD5E1' }
const KIND_LABEL: Record<string, string> = { dormitory: 'общежитие', library: 'библиотека', sports: 'спорт', academic: 'учебный корпус', student_life: 'кафе / столовая', other: 'здание' }
const POI_COLOR: Record<string, string> = { cafe: '#D97706', restaurant: '#EA580C', supermarket: '#0891B2', pharmacy: '#DC2626', bank: '#4B5563', hospital: '#DC2626', cinema: '#7C3AED', park: '#15803D', sports: '#16A34A', library: '#0284C7', bus: '#2563EB', rail: '#1D4ED8' }
const matchColor = (table: Record<string, string>, fallback: string) => ['match', ['get', 'kind'], ...Object.entries(table).flat(), fallback] as unknown as ExpressionSpecification

export function CampusMap3D({ uni, campus, photos, ctx, onOpen }: { uni: University; campus?: Campus | null; photos: Photo[]; ctx: ContextPack | null; onOpen: (p: Photo) => void }) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MLMap | null>(null)
  const [ready, setReady] = useState(false)
  const [showPoi, setShowPoi] = useState(true)

  useEffect(() => {
    if (!container.current || uni.lat == null || uni.lon == null) return
    let cancelled = false
    let map: MLMap | null = null
    loadSatelliteStyle(false).catch(() => 'https://tiles.openfreemap.org/styles/liberty' as const).then((style) => {
      if (cancelled || !container.current) return
      map = new MLMap({ container: container.current, style, center: [uni.lon!, uni.lat!], zoom: 15.6, pitch: 55, bearing: -17, attributionControl: { compact: true }, canvasContextAttributes: { antialias: true }, maxPitch: 70 })
      mapRef.current = map
      map.on('style.load', () => {
        if (!map) return
        if (campus?.polygon) {
          map.addSource('campus', { type: 'geojson', data: { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [campus.polygon.map(([la, lo]) => [lo, la])] } } })
          map.addLayer({ id: 'campus-fill', type: 'fill', source: 'campus', paint: { 'fill-color': '#1D4ED8', 'fill-opacity': 0.08 } })
          map.addLayer({ id: 'campus-line', type: 'line', source: 'campus', paint: { 'line-color': '#1D4ED8', 'line-width': 2.5 } })
          const b = campus.bbox
          map.fitBounds([[b[1], b[0]], [b[3], b[2]]], { padding: 40, pitch: 55, bearing: -17, duration: 0, maxZoom: 17 })
        }
        map.addSource('photos', { type: 'geojson', data: { type: 'FeatureCollection', features: photos.filter((p) => p.lat != null && p.lon != null).map((p) => ({ type: 'Feature', properties: { id: p.id, inside: !!p.geo_inside, title: p.title ?? '' }, geometry: { type: 'Point', coordinates: [p.lon!, p.lat!] } })) } })
        map.addLayer({ id: 'photos', type: 'circle', source: 'photos', paint: { 'circle-radius': 7, 'circle-color': ['case', ['get', 'inside'], '#15803D', '#B45309'], 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } })
        map.on('click', 'photos', (e: MapLayerMouseEvent) => { const id = e.features?.[0]?.properties?.id as string | undefined; const p = photos.find((x) => x.id === id); if (p) onOpen(p) })
        map.on('mouseenter', 'photos', () => { map!.getCanvas().style.cursor = 'pointer' })
        map.on('mouseleave', 'photos', () => { map!.getCanvas().style.cursor = '' })
        setReady(true)
      })
    })
    return () => { cancelled = true; map?.remove(); mapRef.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uni.qid])

  // context layers: coloured campus buildings, route, POIs
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !ctx) return
    const add = () => {
      if (ctx.campus_buildings.features.length && !map.getSource('campus-buildings')) {
        map.addSource('campus-buildings', { type: 'geojson', data: ctx.campus_buildings })
        map.addLayer({ id: 'campus-buildings', type: 'fill-extrusion', source: 'campus-buildings', paint: {
          'fill-extrusion-color': matchColor(KIND_COLOR, '#CBD5E1'),
          'fill-extrusion-height': ['+', ['get', 'height'], 0.6], 'fill-extrusion-base': 0, 'fill-extrusion-opacity': 0.92, 'fill-extrusion-vertical-gradient': true } })
        const popup = new Popup({ closeButton: false, closeOnClick: false, offset: 8 })
        map.on('mousemove', 'campus-buildings', (e: MapLayerMouseEvent) => {
          const f = e.features?.[0]; if (!f) return
          const pr = f.properties as { name?: string; kind: string; levels?: string }
          popup.setLngLat(e.lngLat).setHTML(`<div style="font:12px Inter,sans-serif"><b>${pr.name ?? KIND_LABEL[pr.kind]}</b><br/><span style="color:#6B7280">${KIND_LABEL[pr.kind]}${pr.levels ? ` · ${pr.levels} эт.` : ''}</span></div>`).addTo(map)
        })
        map.on('mouseleave', 'campus-buildings', () => popup.remove())
      }
      if (ctx.route_center.geometry && !map.getSource('route')) {
        map.addSource('route', { type: 'geojson', data: { type: 'Feature', properties: {}, geometry: ctx.route_center.geometry } })
        map.addLayer({ id: 'route', type: 'line', source: 'route', paint: { 'line-color': '#FFFFFF', 'line-width': 3, 'line-dasharray': [2, 2], 'line-opacity': 0.9 } })
      }
      if (ctx.poi.length && !map.getSource('poi')) {
        const feats = ctx.poi.flatMap((g) => g.items.map((i) => ({ type: 'Feature' as const, properties: { kind: g.kind, label: g.label, name: i.name ?? g.label }, geometry: { type: 'Point' as const, coordinates: [i.lon, i.lat] } })))
        map.addSource('poi', { type: 'geojson', data: { type: 'FeatureCollection', features: feats } })
        map.addLayer({ id: 'poi', type: 'circle', source: 'poi', minzoom: 13, paint: { 'circle-radius': 5, 'circle-color': matchColor(POI_COLOR, '#64748B'), 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5, 'circle-opacity': 0.9 } })
        const pp = new Popup({ closeButton: false, closeOnClick: false, offset: 8 })
        map.on('mousemove', 'poi', (e: MapLayerMouseEvent) => { const f = e.features?.[0]; if (!f) return; const pr = f.properties as { name: string; label: string }; pp.setLngLat(e.lngLat).setHTML(`<div style="font:12px Inter,sans-serif"><b>${pr.name}</b><br/><span style="color:#6B7280">${pr.label}</span></div>`).addTo(map) })
        map.on('mouseleave', 'poi', () => pp.remove())
      }
      if (ctx.center.lat != null && ctx.center.lon != null && !map.getSource('center')) {
        map.addSource('center', { type: 'geojson', data: { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [ctx.center.lon, ctx.center.lat] } } })
        map.addLayer({ id: 'center', type: 'circle', source: 'center', paint: { 'circle-radius': 7, 'circle-color': '#0A0A0A', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } })
        map.addLayer({ id: 'center-label', type: 'symbol', source: 'center', layout: { 'text-field': ctx.center.name ?? 'центр', 'text-font': ['Noto Sans Bold'], 'text-size': 12, 'text-offset': [0, 1.2], 'text-anchor': 'top' }, paint: { 'text-color': '#fff', 'text-halo-color': '#0A0A0A', 'text-halo-width': 1.4 } })
      }
    }
    if (map.isStyleLoaded()) add(); else map.once('idle', add)
  }, [ctx, ready])

  useEffect(() => { const m = mapRef.current; if (m && m.getLayer('poi')) m.setLayoutProperty('poi', 'visibility', showPoi ? 'visible' : 'none') }, [showPoi])

  const showRoute = () => {
    const m = mapRef.current
    if (!m || !ctx?.center.lat || !ctx.center.lon || uni.lat == null || uni.lon == null) return
    m.fitBounds([[Math.min(uni.lon, ctx.center.lon), Math.min(uni.lat, ctx.center.lat)], [Math.max(uni.lon, ctx.center.lon), Math.max(uni.lat, ctx.center.lat)]], { padding: 80, pitch: 45, duration: 1600 })
  }
  const recenter = () => {
    const m = mapRef.current
    if (!m) return
    if (campus?.polygon) { const b = campus.bbox; m.fitBounds([[b[1], b[0]], [b[3], b[2]]], { padding: 40, pitch: 55, bearing: -17, duration: 1200, maxZoom: 17 }) }
    else if (uni.lat != null && uni.lon != null) m.flyTo({ center: [uni.lon, uni.lat], zoom: 15.6, pitch: 55, bearing: -17 })
  }
  const counts = campus?.counts ?? {}
  return (
    <div className="space-y-3">
      <div className="relative rounded-lg overflow-hidden border border-line h-[560px] bg-slate-900">
        <div ref={container} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
        <div className="absolute top-3 left-3 flex gap-2">
          <button onClick={recenter} className="btn-ghost !h-8 !px-3 !bg-white text-xs"><Locate size={13} /> Кампус</button>
          {ctx?.route_center.geometry && <button onClick={showRoute} className="btn-ghost !h-8 !px-3 !bg-white text-xs"><Route size={13} /> Маршрут до центра</button>}
          {ctx && ctx.poi.length > 0 && <label className="btn-ghost !h-8 !px-3 !bg-white text-xs"><input type="checkbox" checked={showPoi} onChange={(e) => setShowPoi(e.target.checked)} className="accent-ink" /> POI</label>}
        </div>
        {!ready && <div className="absolute inset-0 grid place-items-center text-white/70 text-sm">Загружаем 3D-карту…</div>}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {Object.entries(KIND_LABEL).filter(([k]) => (counts[k] ?? 0) > 0 || k === 'other').map(([k, l]) => (
          <span key={k} className="inline-flex items-center gap-1.5"><i className="w-2.5 h-2.5 rounded-sm" style={{ background: KIND_COLOR[k] }} />{l}{counts[k] ? <span className="mono">{counts[k]}</span> : null}</span>
        ))}
        <span className="inline-flex items-center gap-1.5"><i className="w-2.5 h-2.5 rounded-full bg-verified" />фото в кампусе</span>
        <span className="inline-flex items-center gap-1.5"><i className="w-2.5 h-2.5 rounded-full bg-likely" />фото рядом</span>
        <span className="ml-auto">{campus?.mode === 'polygon' ? 'границы и здания — OpenStreetMap' : 'границы не найдены, радиус 500 м'}{campus?.osm_url && <> · <a href={campus.osm_url} target="_blank" rel="noreferrer" className="hover:text-brand">OSM ↗</a></>}</span>
      </div>
    </div>
  )
}

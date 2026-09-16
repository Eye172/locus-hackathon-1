import { MapContainer, TileLayer, Polygon, Circle, CircleMarker, Polyline, Tooltip, Popup } from 'react-leaflet'
import type { Campus, Photo, University } from '../lib/types'
import { thumbUrl } from '../lib/api'
import { useT } from '../lib/i18n'

const KIND_COLOR: Record<string, string> = {
  dormitory: '#7c3aed', library: '#0ea5e9', sports: '#16a34a', academic: '#4f46e5', student_life: '#f59e0b', other: '#94a3b8',
}
const KIND_LABEL: Record<string, string> = {
  dormitory: 'общежитие', library: 'библиотека', sports: 'спорт', academic: 'учебный корпус', student_life: 'кафе/столовая', other: 'здание',
}

export function CampusMap({ uni, campus, photos, onOpen }: { uni: University; campus?: Campus | null; photos: Photo[]; onOpen: (p: Photo) => void }) {
  const t = useT()
  if (uni.lat == null || uni.lon == null) return null
  const center: [number, number] = [uni.lat, uni.lon]
  const geo = photos.filter((p) => p.lat != null && p.lon != null)
  const hasCity = uni.city_lat != null && uni.city_lon != null
  const named = (campus?.buildings ?? []).filter((b) => b.kind !== 'other' || b.name)
  return (
    <div className="space-y-3">
      <div className="card overflow-hidden h-[520px]">
        <MapContainer center={center} zoom={campus?.mode === 'polygon' ? 15 : 14} style={{ height: '100%' }}>
          <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
          {campus?.polygon ? (
            <Polygon positions={campus.polygon as [number, number][]} pathOptions={{ color: '#4f46e5', weight: 2.5, fillOpacity: 0.07 }}>
              <Tooltip sticky>{t('map.polygon')}</Tooltip>
            </Polygon>
          ) : (
            <Circle center={center} radius={campus?.radius_m ?? 500} pathOptions={{ color: '#d97706', dashArray: '6 6', fillOpacity: 0.05 }}>
              <Tooltip sticky>{t('map.radius')}</Tooltip>
            </Circle>
          )}
          {named.map((b) => (
            <CircleMarker key={b.osm_id} center={[b.lat, b.lon]} radius={5} pathOptions={{ color: '#fff', weight: 1.5, fillColor: KIND_COLOR[b.kind], fillOpacity: 0.95 }}>
              <Tooltip>{b.name_en || b.name || KIND_LABEL[b.kind]} · {KIND_LABEL[b.kind]}</Tooltip>
            </CircleMarker>
          ))}
          {geo.map((p) => (
            <CircleMarker key={p.id} center={[p.lat!, p.lon!]} radius={8} pathOptions={{ color: '#fff', weight: 2, fillColor: p.geo_inside ? '#16a34a' : '#d97706', fillOpacity: 1 }}>
              <Popup>
                <button onClick={() => onOpen(p)} className="block w-40 cursor-pointer">
                  <img src={thumbUrl(p)} alt="" className="w-40 h-28 object-cover rounded-lg" />
                  <div className="mt-1 text-xs">{p.title || p.source_label}</div>
                </button>
              </Popup>
            </CircleMarker>
          ))}
          {hasCity && (
            <>
              <Polyline positions={[center, [uni.city_lat!, uni.city_lon!]]} pathOptions={{ color: '#0f172a', weight: 1.5, dashArray: '4 6' }} />
              <CircleMarker center={[uni.city_lat!, uni.city_lon!]} radius={6} pathOptions={{ color: '#0f172a', fillColor: '#fff', fillOpacity: 1 }}>
                <Tooltip permanent direction="right">{t('map.center')}: {uni.city}</Tooltip>
              </CircleMarker>
            </>
          )}
        </MapContainer>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {Object.entries(KIND_LABEL).filter(([k]) => (campus?.counts?.[k] ?? 0) > 0).map(([k, l]) => (
          <span key={k} className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full" style={{ background: KIND_COLOR[k] }} />{l} · {campus?.counts?.[k]}</span>
        ))}
        <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-verified" />фото с геометкой в кампусе</span>
        <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-likely" />фото рядом</span>
        {campus?.osm_url && <a href={campus.osm_url} target="_blank" rel="noreferrer" className="ml-auto hover:text-brand">OpenStreetMap ↗</a>}
      </div>
    </div>
  )
}

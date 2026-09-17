import { useParams } from 'react-router-dom'
import { Campus3D } from '../components/Campus3D'

/** /map3d/:qid — the Google 3D campus map as its own page. */
export default function Map3D() {
  const { qid = '' } = useParams()
  return <Campus3D key={qid} qid={qid} variant="page" />
}

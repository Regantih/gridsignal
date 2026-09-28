import { Link } from 'react-router-dom'
import { EmptyState } from '@/components/states'
import { Button } from '@/components/ui/button'

export function NotFoundPage() {
  return (
    <EmptyState
      title="There is no screen here"
      body="The fleet is fine; this address is not one GridSignal knows."
      action={
        <Button asChild variant="outline">
          <Link to="/">Back to Tonight</Link>
        </Button>
      }
    />
  )
}

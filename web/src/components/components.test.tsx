import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Metric } from './metric'
import { EmptyState, ErrorState } from './states'
import { IncidentBadge, RiskBadge } from './status'
import { plain } from '@/pages/member'

describe('Metric', () => {
  it('renders the value it is given and a skeleton while loading', () => {
    render(<Metric label="Dollars at risk" value="$12.34" testId="m" />)
    expect(screen.getByTestId('m')).toHaveTextContent('$12.34')
    const { container } = render(<Metric label="x" value="y" loading testId="m2" />)
    expect(screen.queryByTestId('m2')).toBeNull()
    expect(container.querySelector('[aria-hidden]')).not.toBeNull()
  })
})

describe('states', () => {
  it('renders an error with a retry action', () => {
    const retry = vi.fn()
    render(<ErrorState error={new Error('boom')} retry={retry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('boom')
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(retry).toHaveBeenCalledOnce()
  })
  it('renders an empty state', () => {
    render(<EmptyState title="No incidents yet" body="Quiet" />)
    expect(screen.getByText('No incidents yet')).toBeInTheDocument()
  })
})

describe('status badges', () => {
  it('names incident statuses for operators', () => {
    render(<IncidentBadge status="awaiting_approval" />)
    expect(screen.getByText('Waiting for your approval')).toBeInTheDocument()
  })
  it('passes risk statuses through unchanged', () => {
    render(<RiskBadge status="Promise at risk" />)
    expect(screen.getByText('Promise at risk')).toBeInTheDocument()
  })
})

describe('member plain language', () => {
  it('never leaks incident ids or device ids', () => {
    const out = plain('human_approval', 'Recovery plan approved for INC-0001', 'BAT-042')
    expect(out).not.toMatch(/INC-/)
    const other = plain('other', 'BAT-042 something INC-0007 happened', 'BAT-042')
    expect(other).not.toMatch(/INC-|BAT-/)
    expect(other).toBe('Your battery something happened')
  })
})

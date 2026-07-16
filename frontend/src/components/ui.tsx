import { Component, type ButtonHTMLAttributes, type ErrorInfo, type HTMLAttributes, type ReactNode } from 'react'
import { Dialog as BaseDialog } from '@base-ui/react/dialog'
import { AlertCircle, Check, X } from 'lucide-react'
import { clsx } from 'clsx'

export function Button({ className, variant = 'primary', size = 'md', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'icon'
}) {
  return <button className={clsx('button', `button-${variant}`, `button-${size}`, className)} {...props} />
}

export function Badge({ children, tone = 'neutral', className, ...props }: HTMLAttributes<HTMLSpanElement> & {
  tone?: string
}) {
  return <span className={clsx('badge', `badge-${tone}`, className)} {...props}>{children}</span>
}

export function ProgressBar({ value, max = 100, label }: { value: number; max?: number; label?: string }) {
  const percentage = max ? Math.min(100, Math.round((value / max) * 100)) : 0
  return (
    <div
      className="progress-wrap"
      role="progressbar"
      aria-label={label ?? `${percentage}% complete`}
      aria-valuemin={0}
      aria-valuemax={Math.max(max, 1)}
      aria-valuenow={Math.min(Math.max(value, 0), Math.max(max, 1))}
      aria-valuetext={label ?? `${percentage}% complete`}
    >
      <div className="progress-track"><span style={{ width: `${percentage}%` }} /></div>
      {label && <span className="progress-label">{label}</span>}
    </div>
  )
}

export function EmptyState({ icon, title, description, action }: {
  icon?: ReactNode
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-icon">{icon}</div>}
      <h3>{title}</h3>
      <p>{description}</p>
      {action && <div className="empty-actions">{action}</div>}
    </div>
  )
}

export function Dialog({ open, onClose, title, description, children, wide = false }: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  wide?: boolean
}) {
  if (!open) return null
  return (
    <BaseDialog.Root open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose() }}>
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="dialog-backdrop" />
        <BaseDialog.Popup className={clsx('dialog', wide && 'dialog-wide')} aria-modal="true">
        <header className="dialog-header">
          <div>
            <BaseDialog.Title>{title}</BaseDialog.Title>
            {description && <BaseDialog.Description>{description}</BaseDialog.Description>}
          </div>
          <BaseDialog.Close className="button button-ghost button-icon" aria-label="Close dialog"><X size={19} /></BaseDialog.Close>
        </header>
        <div className="dialog-body">{children}</div>
        </BaseDialog.Popup>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  )
}

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={clsx('field', className)}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

export function Notice({ children, tone = 'info' }: { children: ReactNode; tone?: 'info' | 'success' | 'warning' | 'danger' }) {
  return (
    <div className={clsx('notice', `notice-${tone}`)} role={tone === 'danger' ? 'alert' : 'status'} aria-live={tone === 'danger' ? 'assertive' : 'polite'}>
      {tone === 'success' ? <Check size={17} /> : <AlertCircle size={17} />}
      <div>{children}</div>
    </div>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return <div className="spinner-wrap" role="status" aria-live="polite"><span className="spinner" aria-hidden="true" /><span>{label}</span></div>
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : 'Something went wrong.'
  return (
    <div className="error-state" role="alert">
      <AlertCircle size={22} />
      <div><strong>Unable to load this view</strong><p>{message}</p></div>
      {retry && <Button variant="secondary" size="sm" onClick={retry}>Try again</Button>}
    </div>
  )
}

export class ViewErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // Rendering is intentionally contained to the current project view.
  }

  componentDidUpdate(previous: { resetKey: string }) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })
  }

  render() {
    if (this.state.error) {
      return <ErrorState error={this.state.error} retry={() => this.setState({ error: null })} />
    }
    return this.props.children
  }
}

import { useEffect, useRef, type ReactNode } from 'react'

interface ModalProps {
  label: string
  onClose: () => void
  children: ReactNode
}

// A native <dialog> opened with showModal(): focus trap, Esc to close and inert background come from the browser.
export function Modal({ label, onClose, children }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog || dialog.open) return
    if (typeof dialog.showModal === 'function') dialog.showModal()
    else dialog.setAttribute('open', '') // jsdom and very old browsers
    return () => {
      if (dialog.open && typeof dialog.close === 'function') dialog.close()
    }
  }, [])

  return (
    <dialog
      ref={ref}
      className="modal"
      aria-label={label}
      // Click on the backdrop (the dialog element itself) closes it; the Esc key fires `cancel`.
      onClick={(e) => { if (e.target === ref.current) onClose() }}
      onCancel={(e) => { e.preventDefault(); onClose() }}
    >
      {children}
    </dialog>
  )
}

interface ConfirmProps {
  title: string
  message: string
  confirmLabel: string
  cancelLabel?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({ title, message, confirmLabel, cancelLabel = 'Cancel', danger, onConfirm, onCancel }: ConfirmProps) {
  return (
    <Modal label={title} onClose={onCancel}>
      <div className="modal-body">
        <h2>{title}</h2>
        <p style={{ margin: 0 }}>{message}</p>
      </div>
      <div className="modal-actions">
        <button type="button" className="btn" autoFocus onClick={onCancel}>{cancelLabel}</button>
        <button type="button" className={`btn ${danger ? 'danger solid' : 'primary'}`} onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </Modal>
  )
}

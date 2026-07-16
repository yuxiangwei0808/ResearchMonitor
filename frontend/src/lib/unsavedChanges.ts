export const UNSAVED_CHANGES_MESSAGE = 'Discard your unsaved changes?'

export function requestCloseWithUnsavedChanges(dirty: boolean, onClose: () => void) {
  if (dirty && !window.confirm(UNSAVED_CHANGES_MESSAGE)) return false
  onClose()
  return true
}

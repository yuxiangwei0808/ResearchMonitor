import { ContextMenu, Menu } from '@base-ui/react'
import { Edit3, ListTree, MoreHorizontal, Plus, Trash2 } from 'lucide-react'
import { Fragment, type ReactElement, type ReactNode } from 'react'
import { clsx } from 'clsx'
import type { Task } from '../types'

export type TaskActionProps = {
  task: Task
  onEdit: () => void
  onAddSubtask: () => void
  onDelete: () => void
  onOpenSubtasks?: () => void
  childCount?: number
}

type Action = {
  label: string
  icon: ReactNode
  run: () => void
  danger?: boolean
}

function actions({ onEdit, onAddSubtask, onDelete, onOpenSubtasks, childCount = 0 }: TaskActionProps): Action[] {
  const result: Action[] = [
    { label: 'Edit task', icon: <Edit3 size={14} />, run: onEdit },
    { label: 'Add subtask', icon: <Plus size={14} />, run: onAddSubtask },
  ]
  if (onOpenSubtasks && childCount > 0) {
    result.push({
      label: 'View ' + childCount + ' subtask' + (childCount === 1 ? '' : 's'),
      icon: <ListTree size={14} />,
      run: onOpenSubtasks,
    })
  }
  result.push({ label: 'Delete task', icon: <Trash2 size={14} />, run: onDelete, danger: true })
  return result
}

function TaskActionItems({ variant, ...props }: TaskActionProps & { variant: 'button' | 'context' }) {
  const Item = variant === 'button' ? Menu.Item : ContextMenu.Item
  return actions(props).map((action, index, values) => (
    <Fragment key={action.label}>
      {action.danger && index > 0 && !values[index - 1].danger && (
        variant === 'button'
          ? <Menu.Separator className="task-action-separator" />
          : <ContextMenu.Separator className="task-action-separator" />
      )}
      <Item className={clsx('task-action-item', action.danger && 'danger')} onClick={action.run}>
        {action.icon}<span>{action.label}</span>
      </Item>
    </Fragment>
  ))
}

function MenuPopup(props: TaskActionProps) {
  return (
    <Menu.Portal>
      <Menu.Positioner className="task-action-positioner" side="bottom" align="end" sideOffset={5}>
        <Menu.Popup className="task-action-popup" aria-label={'Actions for ' + props.task.title}>
          <TaskActionItems variant="button" {...props} />
        </Menu.Popup>
      </Menu.Positioner>
    </Menu.Portal>
  )
}

export function TaskActionsButton(props: TaskActionProps) {
  return (
    <Menu.Root>
      <Menu.Trigger
        className="task-action-trigger nodrag nopan"
        aria-label={'Actions for ' + props.task.title}
        title="Task actions"
        onClick={(event) => event.stopPropagation()}
      >
        <MoreHorizontal size={16} />
      </Menu.Trigger>
      <MenuPopup {...props} />
    </Menu.Root>
  )
}

export function TaskContextRegion({ children, ...props }: TaskActionProps & { children: ReactElement }) {
  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger render={children} />
      <ContextMenu.Portal>
        <ContextMenu.Positioner className="task-action-positioner">
          <ContextMenu.Popup className="task-action-popup" aria-label={'Actions for ' + props.task.title}>
            <TaskActionItems variant="context" {...props} />
          </ContextMenu.Popup>
        </ContextMenu.Positioner>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  )
}

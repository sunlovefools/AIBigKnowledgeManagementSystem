import { useEffect, useMemo, useRef, useState } from 'react'
import { useEditor, EditorContent } from '@tiptap/react'
import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import StarterKit from '@tiptap/starter-kit'
import { Table } from '@tiptap/extension-table'
import { TableRow } from '@tiptap/extension-table-row'
import { TableHeader } from '@tiptap/extension-table-header'
import { TableCell } from '@tiptap/extension-table-cell'
import type { Node as ProseMirrorNode } from '@tiptap/pm/model'
import type { ProposalHunk, ProposalStatus } from '../types'
import {
  htmlToEditorMarkdown,
  normalizeEditorHtmlForMarkdown,
  renderMarkdownToEditorHtml,
} from '../utils/markdownEditor'

export type MarkdownReviewMarker = {
  proposalKey: string
  parentId: string
  status: ProposalStatus
  hunks: ProposalHunk[]
  offset: number
}

type MarkdownReviewCallbacks = {
  onAccept: (parentId: string) => void
  onReject: (parentId: string) => void
  onUndo: (parentId: string) => void
}

// Type definitions for the MarkdownEditor component props
type MarkdownEditorProps = {
  markdown: string
  editable?: boolean // To toggle between read-only and editable mode
  onChange?: (nextContent: string) => void // Callback to emit markdown content changes
  className?: string
  reviewMarkers?: MarkdownReviewMarker[]
  reviewCallbacks?: MarkdownReviewCallbacks
}

const EMPTY_REVIEW_MARKERS: MarkdownReviewMarker[] = []
const reviewDecorationsKey = new PluginKey<DecorationSet>('document-review-decorations')

const ReviewDecorationExtension = Extension.create({
  name: 'documentReviewDecorations',

  addProseMirrorPlugins() {
    return [
      new Plugin<DecorationSet>({
        key: reviewDecorationsKey,
        state: {
          init: () => DecorationSet.empty,
          apply(transaction, previous) {
            const nextDecorations = transaction.getMeta(reviewDecorationsKey) as DecorationSet | undefined
            if (nextDecorations) return nextDecorations
            return previous.map(transaction.mapping, transaction.doc)
          },
        },
        props: {
          decorations(state) {
            return reviewDecorationsKey.getState(state) ?? DecorationSet.empty
          },
        },
      }),
    ]
  },
})

function buildDocOffsetMap(doc: ProseMirrorNode): Array<number | null> {
  const map: Array<number | null> = []
  let hasBlockText = false

  doc.descendants((node, pos) => {
    if (node.isBlock && pos > 0) {
      if (hasBlockText) {
        map.push(null, null)
      }
      hasBlockText = true
    }

    if (!node.isText || !node.text) return
    for (let index = 0; index < node.text.length; index += 1) {
      map.push(pos + index)
    }
  })

  return map
}

function plainOffsetToDocPosition(
  offsetMap: Array<number | null>,
  offset: number,
  bias: 'forward' | 'backward'
): number | null {
  if (offsetMap.length === 0) return null

  const clamped = Math.max(0, Math.min(offset, offsetMap.length))
  if (bias === 'forward') {
    for (let index = clamped; index < offsetMap.length; index += 1) {
      const position = offsetMap[index]
      if (typeof position === 'number') return position
    }
    for (let index = clamped - 1; index >= 0; index -= 1) {
      const position = offsetMap[index]
      if (typeof position === 'number') return position + 1
    }
    return null
  }

  for (let index = clamped - 1; index >= 0; index -= 1) {
    const position = offsetMap[index]
    if (typeof position === 'number') return position + 1
  }
  for (let index = clamped; index < offsetMap.length; index += 1) {
    const position = offsetMap[index]
    if (typeof position === 'number') return position
  }
  return null
}

function makeSuggestionWidget(
  marker: MarkdownReviewMarker,
  hunk: ProposalHunk,
  isActive: boolean,
  callbacks?: MarkdownReviewCallbacks,
  onActivate?: (proposalKey: string) => void
) {
  const widget = document.createElement('span')
  widget.className = `review-suggestion-widget ${marker.status} ${isActive ? 'active' : ''}`.trim()
  widget.dataset.proposalKey = marker.proposalKey
  widget.contentEditable = 'false'

  const pill = document.createElement('button')
  pill.type = 'button'
  pill.className = 'review-suggestion-pill'
  pill.textContent = marker.status === 'accepted'
    ? 'Accepted'
    : hunk.type === 'insert'
      ? `Add ${hunk.proposedText.trim() || 'text'}`
      : hunk.type === 'delete'
        ? 'Delete'
        : `Change to ${hunk.proposedText.trim() || 'new text'}`
  pill.addEventListener('click', (event) => {
    event.preventDefault()
    event.stopPropagation()
    onActivate?.(marker.proposalKey)
  })
  widget.appendChild(pill)

  const actions = document.createElement('span')
  actions.className = 'review-suggestion-actions'

  if (marker.status === 'pending') {
    const accept = document.createElement('button')
    accept.type = 'button'
    accept.className = 'review-action review-action-accept'
    accept.textContent = 'Accept'
    accept.addEventListener('click', (event) => {
      event.preventDefault()
      event.stopPropagation()
      callbacks?.onAccept(marker.parentId)
    })

    const reject = document.createElement('button')
    reject.type = 'button'
    reject.className = 'review-action review-action-reject'
    reject.textContent = 'Reject'
    reject.addEventListener('click', (event) => {
      event.preventDefault()
      event.stopPropagation()
      callbacks?.onReject(marker.parentId)
    })

    actions.append(accept, reject)
  } else {
    const undo = document.createElement('button')
    undo.type = 'button'
    undo.className = 'review-action review-action-undo'
    undo.textContent = 'Undo'
    undo.addEventListener('click', (event) => {
      event.preventDefault()
      event.stopPropagation()
      callbacks?.onUndo(marker.parentId)
    })
    actions.appendChild(undo)
  }

  widget.appendChild(actions)
  return widget
}

function buildReviewDecorations(
  doc: ProseMirrorNode,
  markers: MarkdownReviewMarker[],
  activeReviewKey: string | null,
  callbacks?: MarkdownReviewCallbacks,
  onActivate?: (proposalKey: string) => void
) {
  if (!markers.length) return DecorationSet.empty

  const offsetMap = buildDocOffsetMap(doc)
  const decorations: Decoration[] = []

  for (const marker of markers) {
    const isActive = marker.proposalKey === activeReviewKey
    marker.hunks.forEach((hunk, hunkIndex) => {
      const hunkStart = marker.offset + (marker.status === 'accepted' ? hunk.proposedStart : hunk.originalStart)
      const hunkEnd = marker.offset + (marker.status === 'accepted' ? hunk.proposedEnd : hunk.originalEnd)
      const from = plainOffsetToDocPosition(offsetMap, hunkStart, 'forward')
      const to = plainOffsetToDocPosition(offsetMap, hunkEnd, 'backward') ?? from

      if (typeof from !== 'number') return

      if (typeof to === 'number' && to > from) {
        decorations.push(Decoration.inline(from, to, {
          class: `review-text-marker ${marker.status} ${hunk.type} ${isActive ? 'active' : ''}`.trim(),
          'data-proposal-key': marker.proposalKey,
        }))
      }

      const widgetPosition = typeof to === 'number' && to >= from ? to : from
      decorations.push(Decoration.widget(
        widgetPosition,
        () => makeSuggestionWidget(marker, hunk, isActive, callbacks, onActivate),
        {
          key: `${marker.proposalKey}-${hunkIndex}-${marker.status}-${isActive ? 'active' : 'idle'}`,
          side: 1,
        }
      ))
    })
  }

  return DecorationSet.create(doc, decorations)
}

export default function MarkdownEditor({
  markdown,
  editable = false,
  onChange,
  className,
  reviewMarkers = EMPTY_REVIEW_MARKERS,
  reviewCallbacks,
}: MarkdownEditorProps) {
  const lastEmittedContentRef = useRef<string | null>(null)
  const [activeReviewKey, setActiveReviewKey] = useState<string | null>(null)

  // Whenever the markdown prop changes, then convert it to HTML and update the editor content.
  const html = useMemo(() => {
    return renderMarkdownToEditorHtml(markdown)
  }, [markdown])

  // Creats the actual editor instance using the useEditor hook from tiptap
  const editor = useEditor({
    extensions: [
      StarterKit,
      Table.configure({
        resizable: true,
      }),
      TableRow,
      TableHeader,
      TableCell,
      ReviewDecorationExtension,
    ],
    content: html,
    editable,
    // The saving mechanism, whenever there is an update in the editor content
    // Send it to the frontend 
    onUpdate: ({ editor: currentEditor }) => {
      if (!editable || !onChange) return

      const normalizedHtml = normalizeEditorHtmlForMarkdown(currentEditor.getHTML())
      const nextContent = htmlToEditorMarkdown(normalizedHtml)

      if (lastEmittedContentRef.current === nextContent) return

      lastEmittedContentRef.current = nextContent
      onChange(nextContent)
    },
    immediatelyRender: false,
  })

  useEffect(() => {
    if (!editor) return
    editor.setEditable(editable)
  }, [editor, editable])

  useEffect(() => {
    if (!editor) return
    if (markdown === lastEmittedContentRef.current) {
      lastEmittedContentRef.current = null
      return
    }

    editor.commands.setContent(html, false)
  }, [editor, html, markdown])

  useEffect(() => {
    if (!reviewMarkers.length) {
      setActiveReviewKey((current) => (current === null ? current : null))
      return
    }

    setActiveReviewKey((current) => {
      if (current && reviewMarkers.some((marker) => marker.proposalKey === current)) {
        return current
      }
      return reviewMarkers.find((marker) => marker.status === 'pending')?.proposalKey ?? reviewMarkers[0].proposalKey
    })
  }, [reviewMarkers])

  useEffect(() => {
    if (!editor) return
    const decorations = buildReviewDecorations(
      editor.state.doc,
      editable ? [] : reviewMarkers,
      activeReviewKey,
      reviewCallbacks,
      setActiveReviewKey
    )
    editor.view.dispatch(editor.state.tr.setMeta(reviewDecorationsKey, decorations))
  }, [activeReviewKey, editable, editor, reviewCallbacks, reviewMarkers, html])

  return (
    <div className={`md-editor-wrapper ${editable ? 'is-editable' : 'is-readonly'} ${className ?? ''}`.trim()}>
      <EditorContent editor={editor} className="md-editor" />
    </div>
  )
}

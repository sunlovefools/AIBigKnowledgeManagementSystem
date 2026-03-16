import { useEffect, useMemo, useRef } from 'react'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { Table } from '@tiptap/extension-table'
import { TableRow } from '@tiptap/extension-table-row'
import { TableHeader } from '@tiptap/extension-table-header'
import { TableCell } from '@tiptap/extension-table-cell'
import { marked } from 'marked'
import {
  htmlToEditorMarkdown,
  normalizeEditorHtmlForMarkdown,
} from '../utils/markdownEditor'

// Type definitions for the MarkdownEditor component props
type MarkdownEditorProps = {
  markdown: string
  editable?: boolean // To toggle between read-only and editable mode
  onChange?: (nextContent: string) => void // Callback to emit markdown content changes
  className?: string
}

// Main function component that is exported for use in other parts of the application
export default function MarkdownEditor({
  markdown,
  editable = false,
  onChange,
  className,
}: MarkdownEditorProps) {
  const lastEmittedContentRef = useRef<string | null>(null)

  // Whenever the markdown prop changes, then convert it to HTML and update the editor content. 
  const html = useMemo(() => {
    return marked.parse(markdown) as string
  }, [markdown])

  // Creats the actual editor instance using the useEditor hook from tiptap
  const editor = useEditor({
    extensions: [ // Plugins/extension for the editor for rendering
      StarterKit,
      Table.configure({
        resizable: true,
      }),
      TableRow,
      TableHeader,
      TableCell,
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

  return (
    <div className={`md-editor-wrapper ${editable ? 'is-editable' : 'is-readonly'} ${className ?? ''}`.trim()}>
      <EditorContent editor={editor} className="md-editor" />
    </div>
  )
}

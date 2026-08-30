import { useEffect, useRef, useState } from 'react'
import {
  Button, Card, Checkbox, Input, List, Modal, Select, Space, Tag, Typography, message,
} from 'antd'
import { DeleteOutlined, EditOutlined, FolderOpenOutlined, UploadOutlined } from '@ant-design/icons'

const { Text } = Typography

function displayFileName(filePath) {
  const name = (filePath || '').split(/[\\/]/).pop() || ''
  return name.replace(/^[0-9a-f]{32}__/, '')
}

export default function KbManagePage() {
  const [docs, setDocs] = useState([])
  const [folders, setFolders] = useState([])
  const [folder, setFolder] = useState('default')
  const [newFolder, setNewFolder] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState('')
  const [selectedDocId, setSelectedDocId] = useState(null)
  const [chunks, setChunks] = useState([])
  const [editingChunk, setEditingChunk] = useState(null)
  const [editText, setEditText] = useState('')
  const [splittingChunk, setSplittingChunk] = useState(null)
  const [splitPart1, setSplitPart1] = useState('')
  const [splitPart2, setSplitPart2] = useState('')
  const [chunkBusy, setChunkBusy] = useState(false)
  const [selectedIds, setSelectedIds] = useState([])
  const [batchMoveFolder, setBatchMoveFolder] = useState(undefined)
  const [renamingFolder, setRenamingFolder] = useState(null)
  const [renameText, setRenameText] = useState('')
  const [dragDocId, setDragDocId] = useState(null)
  const [expandedFolders, setExpandedFolders] = useState({})
  const fileInputRef = useRef(null)
  const folderInputRef = useRef(null)

  useEffect(() => { loadDocs(); loadFolders() }, [])

  function loadDocs() {
    const url = '/api/kb/docs'
    fetch(url).then((r) => r.json()).then((d) => setDocs(d.data || []))
  }
  function loadFolders() {
    fetch('/api/kb/folders').then((r) => r.json()).then((d) => {
      const fs = d.data || []
      setFolders(fs)
      if (!fs.find((f) => f.kb_id === folder)) setFolder('default')
      setExpandedFolders((prev) => {
        const next = { ...prev }
        for (const f of fs) if (next[f.kb_id] === undefined) next[f.kb_id] = true
        if (next['default'] === undefined) next['default'] = true
        return next
      })
    })
  }

  async function viewChunks(docId) {
    setSelectedDocId(docId)
    const r = await fetch('/api/kb/docs/' + encodeURIComponent(docId) + '/chunks')
    const d = await r.json()
    if (d.ok) { setChunks(d.data || []) } else { message.error(d.error?.message || '获取分块失败'); setChunks([]) }
  }

  async function saveChunkEdit() {
    if (!editingChunk || !editText.trim() || chunkBusy) return
    setChunkBusy(true)
    try {
      const r = await fetch(`/api/kb/docs/${encodeURIComponent(selectedDocId)}/chunks/${encodeURIComponent(editingChunk.chunk_id)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: editText }),
      })
      const d = await r.json()
      if (d.ok) { message.success('已更新分块'); setEditingChunk(null); viewChunks(selectedDocId); loadDocs() } else { message.error(d.error?.message || '更新失败') }
    } catch (e) { message.error('更新失败: ' + e.message) } finally { setChunkBusy(false) }
  }

  function openSplit(c) {
    const text = c.text || ''
    const NL = String.fromCharCode(10)
    const puncts = ['。', '；', ';', '！', '？']
    const mid = Math.floor(text.length / 2)
    let cut = mid
    for (let i = 0; i < 120; i++) {
      const pos = mid + i
      if (pos < text.length && (puncts.includes(text[pos]) || text[pos] === NL)) { cut = pos + 1; break }
      const neg = mid - i
      if (neg > 0 && (puncts.includes(text[neg]) || text[neg] === NL)) { cut = neg + 1; break }
    }
    setSplitPart1(text.slice(0, cut).trim())
    setSplitPart2(text.slice(cut).trim())
    setSplittingChunk(c)
  }

  async function saveChunkSplit() {
    if (!splittingChunk || !splitPart1.trim() || !splitPart2.trim() || chunkBusy) return
    setChunkBusy(true)
    try {
      const r = await fetch(`/api/kb/docs/${encodeURIComponent(selectedDocId)}/chunks/${encodeURIComponent(splittingChunk.chunk_id)}/split`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ part1: splitPart1, part2: splitPart2 }),
      })
      const d = await r.json()
      if (d.ok) { message.success('已拆分分块'); setSplittingChunk(null); viewChunks(selectedDocId); loadDocs() } else { message.error(d.error?.message || '拆分失败') }
    } catch (e) { message.error('拆分失败: ' + e.message) } finally { setChunkBusy(false) }
  }

  async function mergeChunkWithPrev(i) {
    if (i <= 0 || chunkBusy) return
    const c1 = chunks[i - 1]
    const c2 = chunks[i]
    Modal.confirm({
      title: '合并分块',
      content: `将 #${i} 与 #${i + 1} 合并为一段？`,
      okText: '合并', cancelText: '取消',
      onOk: async () => {
        setChunkBusy(true)
        try {
          const r = await fetch(`/api/kb/docs/${encodeURIComponent(selectedDocId)}/chunks/merge`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chunk_id1: c1.chunk_id, chunk_id2: c2.chunk_id }),
          })
          const d = await r.json()
          if (d.ok) { message.success('已合并分块'); viewChunks(selectedDocId); loadDocs() } else { message.error(d.error?.message || '合并失败') }
        } catch (e) { message.error('合并失败: ' + e.message) } finally { setChunkBusy(false) }
      },
    })
  }

  async function deleteChunk(c) {
    if (chunkBusy) return
    Modal.confirm({
      title: '删除分块',
      content: '删除该分块后不可恢复，确认删除？',
      okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        setChunkBusy(true)
        try {
          const r = await fetch(`/api/kb/docs/${encodeURIComponent(selectedDocId)}/chunks/${encodeURIComponent(c.chunk_id)}`, { method: 'DELETE' })
          const d = await r.json()
          if (d.ok) { message.success('已删除分块'); viewChunks(selectedDocId); loadDocs() } else { message.error(d.error?.message || '删除失败') }
        } catch (e) { message.error('删除失败: ' + e.message) } finally { setChunkBusy(false) }
      },
    })
  }

  async function createFolder() {
    const name = newFolder.trim()
    if (!name) return
    const r = await fetch('/api/kb/folders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
    const d = await r.json()
    if (d.ok) { message.success(`已创建文件夹：${name}`); setNewFolder(''); setFolder(name); loadFolders() } else { message.error(d.error?.message || '创建失败') }
  }

  async function saveRenameFolder() {
    const name = (renameText || '').trim()
    if (!renamingFolder || !name) return
    const r = await fetch('/api/kb/folders/' + encodeURIComponent(renamingFolder.kb_id), {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
    })
    const d = await r.json()
    if (d.ok) { message.success(`已改名为：${name}`); setRenamingFolder(null); setFolder(name); loadFolders(); loadDocs() } else { message.error(d.error?.message || '改名失败') }
  }

  async function deleteFolder(f) {
    Modal.confirm({
      title: `删除文件夹「${f.name}」`,
      content: '文件夹内的文档将一并删除，且不可恢复。确认删除？',
      okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        const r = await fetch('/api/kb/folders/' + encodeURIComponent(f.kb_id) + '?cascade=true', { method: 'DELETE' })
        const d = await r.json()
        if (d.ok) { message.success('已删除文件夹'); setFolder('default'); loadFolders(); loadDocs() } else { message.error(d.error?.message || '删除失败') }
      },
    })
  }

  async function moveDoc(docId, targetFolder) {
    const r = await fetch('/api/kb/docs/' + encodeURIComponent(docId) + '/folder', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kb_id: targetFolder }),
    })
    const d = await r.json()
    if (d.ok) { message.success('已移动文档'); loadDocs(); loadFolders() } else { message.error(d.error?.message || '移动失败') }
  }

  async function moveSelectedDocs() {
    if (!selectedIds.length || !batchMoveFolder) return
    const r = await fetch('/api/kb/docs/move', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ doc_ids: selectedIds, kb_id: batchMoveFolder }),
    })
    const d = await r.json()
    if (d.ok) { message.success(`已移动 ${d.data.moved} 篇文档`); setSelectedIds([]); setBatchMoveFolder(undefined); loadDocs(); loadFolders() } else { message.error(d.error?.message || '移动失败') }
  }

  async function uploadOne(file) {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('kb_id', folder || 'default')
    const r = await fetch('/api/kb/upload', { method: 'POST', body: fd })
    const d = await r.json()
    if (d.ok) { message.success(`已上传：${d.data.name}（${d.data.children} chunks）到 ${folder || 'default'}`) } else { message.error(`${file.name}: ` + (d.error?.message || '上传失败')) }
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || [])
    if (!files.length || uploading) return
    setUploading(true)
    let ok = 0
    for (let i = 0; i < files.length; i++) {
      setUploadProgress(`上传中 ${i + 1}/${files.length}: ${files[i].name}`)
      try { await uploadOne(files[i]); ok++ } catch (e) { message.error(`${files[i].name}: ` + e.message) }
    }
    setUploadProgress(`完成：${ok}/${files.length} 个文件`)
    setUploading(false)
    loadDocs(); loadFolders()
  }

  async function removeDoc(docId) {
    await fetch('/api/kb/docs/' + docId, { method: 'DELETE' })
    message.success('已删除')
    if (selectedDocId === docId) { setSelectedDocId(null); setChunks([]) }
    loadDocs(); loadFolders()
  }

  function dropOnFolder(targetFolder) {
    if (!dragDocId) return
    moveDoc(dragDocId, targetFolder)
    setDragDocId(null)
  }

  const docsInFolder = (kbId) => docs.filter((d) => (d.kb_id || 'default') === kbId)

  return (
    <div style={{ display: 'flex', gap: 8, height: 'calc(100vh - 120px)' }}>
      {/* 左：文件目录树 */}
      <Card size="small" style={{ width: 360, flexShrink: 0, display: 'flex', flexDirection: 'column' }} styles={{ body: { flex: 1, overflow: 'auto', padding: 8 } }}
        title="文件目录树"
        extra={
          <Space size={4}>
            <Button size="small" icon={<UploadOutlined />} onClick={() => fileInputRef.current?.click()} disabled={uploading}>上传文件</Button>
            <Button size="small" icon={<FolderOpenOutlined />} onClick={() => folderInputRef.current?.click()} disabled={uploading}>上传文件夹</Button>
          </Space>
        }>
        <Space direction="vertical" style={{ width: '100%' }} size={6}>
          <Space wrap>
            <Input size="small" style={{ width: 150 }} placeholder="新文件夹名" value={newFolder} onChange={(e) => setNewFolder(e.target.value)} onPressEnter={createFolder} />
            <Button size="small" onClick={createFolder}>新建文件夹</Button>
          </Space>
          {uploading && <Tag color="blue">{uploadProgress}</Tag>}
          <Text type="secondary" style={{ fontSize: 12 }}>点击文件夹展开/收起文件列表；上传目标：当前选中文件夹「{folder}」</Text>
          {folders.map((f) => {
            const expanded = expandedFolders[f.kb_id] !== false
            const folderDocs = docsInFolder(f.kb_id)
            return (
              <div key={f.kb_id}>
                <div
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => dropOnFolder(f.kb_id)}
                  onClick={() => { setExpandedFolders((prev) => ({ ...prev, [f.kb_id]: !expanded })); setFolder(f.kb_id) }}
                  style={{ border: dragDocId ? '1px dashed #1677ff' : '1px solid #f0f0f0', borderRadius: 6, padding: '6px 8px', background: dragDocId ? '#f0f5ff' : folder === f.kb_id ? '#fff7e6' : '#fafafa', display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', marginBottom: 2 }}
                >
                  <Space size={4}>
                    <span style={{ color: '#999', fontSize: 12 }}>{expanded ? '▾' : '▸'}</span>
                    <FolderOpenOutlined style={{ color: '#faad14' }} />
                    <Text strong style={{ fontSize: 13 }}>{f.name}</Text>
                    <Tag>{folderDocs.length} 篇</Tag>
                  </Space>
                  <Space size={4} onClick={(e) => e.stopPropagation()}>
                    <Button size="small" type="text" icon={<EditOutlined />} onClick={() => { setRenamingFolder(f); setRenameText(f.name) }} />
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} disabled={f.kb_id === 'default'} onClick={() => deleteFolder(f)} />
                  </Space>
                </div>
                {expanded && (
                  <div style={{ marginLeft: 18, borderLeft: '1px dashed #d9d9d9', paddingLeft: 8 }}>
                    {folderDocs.length === 0 && <Text type="secondary" style={{ fontSize: 12 }}>（空）</Text>}
                    {folderDocs.map((d) => (
                      <div key={d.doc_id}
                        draggable
                        onDragStart={() => setDragDocId(d.doc_id)}
                        onDragEnd={() => setDragDocId(null)}
                        onClick={() => viewChunks(d.doc_id)}
                        style={{ cursor: 'grab', background: d.doc_id === selectedDocId ? '#e6f4ff' : undefined, padding: '4px 8px', borderRadius: 6, marginBottom: 2, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                      >
                        <Space size={4}>
                          <Checkbox checked={selectedIds.includes(d.doc_id)} onClick={(e) => e.stopPropagation()} onChange={(e) => setSelectedIds((prev) => e.target.checked ? [...prev, d.doc_id] : prev.filter((x) => x !== d.doc_id))} />
                          <Text style={{ fontSize: 13, wordBreak: 'break-all' }}>{displayFileName(d.file_path)}</Text>
                        </Space>
                        <Space size={4}>
                          <Tag>{d.parse_status}</Tag>
                          <Text type="secondary" style={{ fontSize: 12 }}>{d.chunk_count} chunks</Text>
                          <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={(e) => { e.stopPropagation(); removeDoc(d.doc_id) }} />
                        </Space>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text strong>批量操作</Text>
            <Space size={4}>
              <Checkbox checked={docs.length > 0 && selectedIds.length === docs.length} onChange={(e) => setSelectedIds(e.target.checked ? docs.map((d) => d.doc_id) : [])}>全选</Checkbox>
              <Select size="small" style={{ width: 130 }} placeholder="移动到文件夹" value={batchMoveFolder} onChange={setBatchMoveFolder} options={folders.map((f) => ({ value: f.kb_id, label: f.name }))} />
              <Button size="small" disabled={!selectedIds.length || !batchMoveFolder} onClick={moveSelectedDocs}>移动选中</Button>
            </Space>
          </div>
        </Space>
      </Card>

      {/* 右：切块预览 */}
      <Card size="small" style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }} styles={{ body: { flex: 1, overflow: 'auto', padding: 8 } }}
        title={selectedDocId ? `分块预览（${displayFileName(docs.find((d) => d.doc_id === selectedDocId)?.file_path || selectedDocId)}）` : '文档切块预览'}
      >
        {!selectedDocId && <Text type="secondary">未选中文档，点击左侧文档查看切块</Text>}
        {selectedDocId && chunks.length === 0 && <Text type="secondary">（无分块）</Text>}
        {selectedDocId && chunks.map((c, i) => (
          <Card key={c.chunk_id} size="small" style={{ marginBottom: 8 }} title={
            <Space size={4} wrap>
              <Text strong style={{ fontSize: 12 }}>#{i + 1}</Text>
              <Tag>{c.chunk_level}</Tag>
              {c.articles && <Tag color="blue">条{c.articles.join(',')}</Tag>}
              {c.folder && <Tag color="orange">{c.folder}</Tag>}
              <Text type="secondary" style={{ fontSize: 11 }}>{c.token_count} tokens</Text>
            </Space>
          } extra={
            <Space size={4}>
              <Button size="small" type="link" onClick={() => { setEditingChunk(c); setEditText(c.text || '') }}>编辑</Button>
              <Button size="small" type="link" onClick={() => openSplit(c)}>拆分</Button>
              {i > 0 && <Button size="small" type="link" onClick={() => mergeChunkWithPrev(i)}>与上一块合并</Button>}
              {chunks.length > 1 && <Button size="small" type="link" danger onClick={() => deleteChunk(c)}>删除</Button>}
            </Space>
          }>
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0, maxHeight: 200, overflow: 'auto' }}>{c.text}</pre>
          </Card>
        ))}
      </Card>

      <input ref={fileInputRef} type="file" accept=".md,.txt,.docx,.pdf" multiple style={{ display: 'none' }}
        onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />
      <input ref={folderInputRef} type="file" webkitdirectory="" directory="" multiple style={{ display: 'none' }}
        onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />

      <Modal open={!!renamingFolder} onCancel={() => setRenamingFolder(null)} onOk={saveRenameFolder} okText="保存" cancelText="取消" title={`文件夹改名（${renamingFolder?.name || ''}）`}>
        <Input value={renameText} onChange={(e) => setRenameText(e.target.value)} placeholder="新文件夹名" />
      </Modal>
      <Modal open={!!editingChunk} onCancel={() => setEditingChunk(null)} onOk={saveChunkEdit} okText="保存" cancelText="取消" confirmLoading={chunkBusy} width={720} title="编辑分块文本">
        <Input.TextArea value={editText} onChange={(e) => setEditText(e.target.value)} autoSize={{ minRows: 8, maxRows: 24 }} />
        <Text type="secondary" style={{ fontSize: 12 }}>保存后该分块将按新文本重新嵌入。</Text>
      </Modal>
      <Modal open={!!splittingChunk} onCancel={() => setSplittingChunk(null)} onOk={saveChunkSplit} okText="拆分" cancelText="取消" confirmLoading={chunkBusy} width={720} title="拆分为两个分块">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text strong>第 1 段：</Text>
          <Input.TextArea value={splitPart1} onChange={(e) => setSplitPart1(e.target.value)} autoSize={{ minRows: 4, maxRows: 12 }} />
          <Text strong>第 2 段：</Text>
          <Input.TextArea value={splitPart2} onChange={(e) => setSplitPart2(e.target.value)} autoSize={{ minRows: 4, maxRows: 12 }} />
        </Space>
      </Modal>
    </div>
  )
}

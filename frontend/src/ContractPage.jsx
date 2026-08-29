import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Button, Card, Checkbox, Dropdown, Empty, Input, List, Modal, Radio, Select, Space, Tabs, Tag, Typography, message,
} from 'antd'
import {
  CommentOutlined, DeleteOutlined, EditOutlined, FileAddOutlined, FileTextOutlined,
  FolderOpenOutlined, LeftOutlined, RightOutlined, SafetyOutlined, ScanOutlined, SendOutlined, UploadOutlined,
} from '@ant-design/icons'

const { Text } = Typography

const MASK_CATEGORIES = [
  { value: 'person_name', label: '人名' },
  { value: 'company_name', label: '企业名' },
  { value: 'credit_code', label: '信用代码' },
  { value: 'phone', label: '电话' },
  { value: 'email', label: '邮箱' },
  { value: 'id_card', label: '身份证号' },
]

const MASK_METHODS = [
  { value: 'mask', label: '中间打码' },
  { value: 'placeholder', label: '占位符' },
  { value: 'hash', label: '哈希值' },
]

function Markdown({ content, style }) {
  return (
    <div style={style} className="md-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || ''}</ReactMarkdown>
    </div>
  )
}

export default function ContractPage() {
  const [contracts, setContracts] = useState([])
  const [selectedCid, setSelectedCid] = useState(null)
  const [selected, setSelected] = useState(null)
  const [versions, setVersions] = useState([])
  const [versionKey, setVersionKey] = useState('')
  const [previewContent, setPreviewContent] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const [rightTab, setRightTab] = useState('mask')

  // 脱敏 tab
  const [maskCategories, setMaskCategories] = useState(['person_name', 'company_name', 'credit_code', 'phone', 'email', 'id_card'])
  const [maskMethod, setMaskMethod] = useState('placeholder')
  const [scanItems, setScanItems] = useState([])
  const [selectedScanIds, setSelectedScanIds] = useState([])
  const [scanning, setScanning] = useState(false)
  const [masking, setMasking] = useState(false)
  const [mappingEntries, setMappingEntries] = useState([])
  const [selectedMapIds, setSelectedMapIds] = useState([])
  const [restoring, setRestoring] = useState(false)
  const [restorePreview, setRestorePreview] = useState(null)
  const [selectionText, setSelectionText] = useState('')
  const [manualCategory, setManualCategory] = useState('manual')
  const [manualItems, setManualItems] = useState([])

  // 审查 tab
  const [chatSessionId, setChatSessionId] = useState(null)
  const [chatMessages, setChatMessages] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatRunning, setChatRunning] = useState(false)
  const [ruleLibrary, setRuleLibrary] = useState([])
  const [selectedRuleFiles, setSelectedRuleFiles] = useState([])
  const [ruleModalOpen, setRuleModalOpen] = useState(false)
  const [ruleModalSelected, setRuleModalSelected] = useState([])
  const [ruleUploading, setRuleUploading] = useState(false)

  // 重命名
  const [renameTarget, setRenameTarget] = useState(null)
  const [renameValue, setRenameValue] = useState('')
  const [renaming, setRenaming] = useState(false)

  const fileInputRef = useRef(null)
  const folderInputRef = useRef(null)
  const ruleInputRef = useRef(null)

  useEffect(() => { loadContracts() }, [])
  useEffect(() => {
    if (folderInputRef.current) {
      folderInputRef.current.setAttribute('webkitdirectory', '')
      folderInputRef.current.setAttribute('directory', '')
    }
  }, [])

  async function loadContracts() {
    try {
      const r = await fetch('/api/contracts')
      const d = await r.json()
      if (d.ok) {
        setContracts(d.data || [])
        if (!selectedCid && d.data?.length) {
          setSelectedCid(d.data[0].contract_id)
          setSelected(d.data[0])
        } else if (selectedCid) {
          const cur = (d.data || []).find((c) => c.contract_id === selectedCid)
          if (cur) setSelected(cur)
        }
      }
    } catch (e) { message.error('加载合同列表失败: ' + e.message) }
  }

  useEffect(() => {
    if (!selectedCid) return
    loadVersions(selectedCid)
    loadMapping(selectedCid)
    loadManualItems(selectedCid)
    loadRuleLibrary()
    loadChatSession(selectedCid)
  }, [selectedCid])

  async function loadVersions(cid) {
    try {
      const r = await fetch(`/api/contracts/${cid}/versions`)
      const d = await r.json()
      if (d.ok) {
        setVersions(d.data || [])
        const original = (d.data || []).find((v) => v.kind === 'original') || (d.data || [])[0]
        if (original) {
          setVersionKey(original.kind + '::' + original.file)
          loadContent(original.kind, original.file)
        } else {
          setVersionKey('')
          setPreviewContent('')
        }
      }
    } catch (e) { message.error('加载版本失败: ' + e.message) }
  }

  async function loadContent(kind, file) {
    if (!selectedCid || !file) return
    setPreviewLoading(true)
    try {
      const r = await fetch(`/api/contracts/${selectedCid}/content?kind=${encodeURIComponent(kind)}&file=${encodeURIComponent(file)}`)
      const d = await r.json()
      if (d.ok) { setPreviewContent(d.data.content) } else { message.error(d.error?.message || '读取失败'); setPreviewContent('') }
    } catch (e) { message.error('读取失败: ' + e.message); setPreviewContent('') } finally { setPreviewLoading(false) }
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []).filter((f) => f.name)
    if (!files.length) return
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    const hide = message.loading('上传中...', 0)
    try {
      const r = await fetch('/api/contracts/upload', { method: 'POST', body: fd })
      const d = await r.json()
      if (d.ok) {
        const ok = (d.data || []).filter((x) => x.ok).length
        message.success(`完成：${ok}/${d.data.length} 份上传成功`)
        await loadContracts()
        if ((d.data || []).some((x) => x.ok)) {
          const first = d.data.find((x) => x.ok)
          setSelectedCid(first.contract_id)
          setSelected({ contract_id: first.contract_id, original_name: first.filename })
        }
      } else { message.error(d.error?.message || '上传失败') }
    } catch (e) { message.error('上传失败: ' + e.message) } finally { hide() }
  }

  async function removeContract(c) {
    await fetch(`/api/contracts/${c.contract_id}`, { method: 'DELETE' })
    message.success('已删除')
    if (selectedCid === c.contract_id) { setSelectedCid(null); setSelected(null); setVersions([]); setPreviewContent('') }
    loadContracts()
  }

  async function submitRename() {
    if (!renameTarget || !renameValue.trim() || renaming) return
    setRenaming(true)
    try {
      const r = await fetch(`/api/contracts/${renameTarget.contract_id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ original_name: renameValue.trim() }),
      })
      const d = await r.json()
      if (d.ok) { message.success('已重命名'); setRenameTarget(null); loadContracts() } else { message.error(d.error?.message || '重命名失败') }
    } catch (e) { message.error('重命名失败: ' + e.message) } finally { setRenaming(false) }
  }

  async function scanPii() {
    if (!selectedCid) return
    setScanning(true)
    try {
      const cats = maskCategories.join(',')
      const r = await fetch(`/api/contracts/${selectedCid}/scan?categories=${encodeURIComponent(cats)}`)
      const d = await r.json()
      if (d.ok) {
        setScanItems(d.data.items || [])
        setSelectedScanIds((d.data.items || []).map((x) => x.id))
        message.success(`扫描到 ${d.data.total} 项待脱敏信息`)
      } else { message.error(d.error?.message || '扫描失败') }
    } catch (e) { message.error('扫描失败: ' + e.message) } finally { setScanning(false) }
  }

  async function loadManualItems(cid) {
    try {
      const r = await fetch(`/api/contracts/${cid}/mask/manual`)
      const d = await r.json()
      if (d.ok) setManualItems(d.data || [])
    } catch (e) { /* ignore */ }
  }

  async function addSelectionToMask() {
    if (!selectionText || !selectedCid) return
    try {
      const r = await fetch(`/api/contracts/${selectedCid}/mask/manual`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: selectionText, category: manualCategory }),
      })
      const d = await r.json()
      if (d.ok) {
        setManualItems((xs) => [...xs, d.data])
        setSelectedScanIds((ids) => [...ids, d.data.id])
        setSelectionText('')
        message.success('已加入脱敏清单')
      } else { message.error(d.error?.message || '加入失败') }
    } catch (e) { message.error('加入失败: ' + e.message) }
  }

  async function removeManualItem(item) {
    if (!selectedCid) return
    await fetch(`/api/contracts/${selectedCid}/mask/manual/${item.id}`, { method: 'DELETE' })
    setManualItems((xs) => xs.filter((x) => x.id !== item.id))
    setSelectedScanIds((ids) => ids.filter((x) => x !== item.id))
  }

  async function loadMapping(cid) {
    try {
      const r = await fetch(`/api/contracts/${cid}/mask/mapping`)
      const d = await r.json()
      if (d.ok) setMappingEntries(d.data.entries || [])
    } catch (e) { /* ignore */ }
  }

  async function confirmMask() {
    if (!selectedCid) return
    const chosen = [...scanItems, ...manualItems].filter((x) => selectedScanIds.includes(x.id))
    if (!chosen.length) { message.warning('请先勾选待脱敏项目'); return }
    setMasking(true)
    try {
      const r = await fetch(`/api/contracts/${selectedCid}/mask`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ categories: maskCategories, method: maskMethod, items: scanItems.filter((x) => selectedScanIds.includes(x.id)), manual_items: manualItems.filter((x) => selectedScanIds.includes(x.id)) }),
      })
      const d = await r.json()
      if (d.ok) {
        message.success(`已脱敏 ${d.data.masked_count} 处，生成 ${d.data.file}`)
        setMappingEntries(d.data.mapping || [])
        setSelectedMapIds((d.data.mapping || []).map((x) => x.id))
        await loadVersions(selectedCid)
        setVersionKey('masked::' + d.data.file)
        setPreviewContent(d.data.content)
      } else { message.error(d.error?.message || '脱敏失败') }
    } catch (e) { message.error('脱敏失败: ' + e.message) } finally { setMasking(false) }
  }

  async function restoreSelected() {
    if (!selectedCid || !selectedMapIds.length) { message.warning('请先勾选要还原的映射配置'); return }
    const file = versions.find((v) => v.kind === 'masked')?.file
    if (!file) { message.warning('请先确认脱敏生成脱敏版'); return }
    setRestoring(true)
    try {
      const entries = mappingEntries.filter((x) => selectedMapIds.includes(x.id))
      const r = await fetch(`/api/contracts/${selectedCid}/mask/restore`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file, entries }),
      })
      const d = await r.json()
      if (d.ok) {
        setRestorePreview(d.data.content)
        message.success('已还原所选配置，可查看预览')
        if (d.data.warnings?.length) message.warning(d.data.warnings.join('; '))
        await loadVersions(selectedCid)
      } else { message.error(d.error?.message || '还原失败') }
    } catch (e) { message.error('还原失败: ' + e.message) } finally { setRestoring(false) }
  }

  function captureSelection() {
    const sel = window.getSelection().toString().trim()
    if (sel) setSelectionText(sel)
  }

  async function loadRuleLibrary() {
    try {
      const r = await fetch('/api/contracts/skills')
      const d = await r.json()
      if (d.ok) setRuleLibrary(d.data.library || [])
    } catch (e) { /* ignore */ }
  }

  async function uploadRuleFiles(fileList) {
    const files = Array.from(fileList || []).filter((f) => f.name)
    if (!files.length) return
    setRuleUploading(true)
    try {
      const fd = new FormData()
      for (const f of files) fd.append('files', f)
      const r = await fetch('/api/contracts/skills', { method: 'POST', body: fd })
      const d = await r.json()
      if (d.ok) { message.success('规则库已更新'); loadRuleLibrary() } else { message.error(d.error?.message || '上传失败') }
    } catch (e) { message.error('上传失败: ' + e.message) } finally { setRuleUploading(false) }
  }

  async function loadChatSession(cid) {
    try {
      const r = await fetch(`/api/sessions?mode=contract_review&action=${encodeURIComponent(cid)}`)
      const d = await r.json()
      if (d.ok && d.data?.length) {
        setChatSessionId(d.data[0].session_id)
        const mr = await fetch(`/api/sessions/${d.data[0].session_id}/messages`)
        const md = await mr.json()
        if (md.ok) setChatMessages((md.data || []).map((m) => ({ role: m.role, content: m.content })))
      } else {
        setChatSessionId(null)
        setChatMessages([])
      }
    } catch (e) { /* ignore */ }
  }

  async function sendChat() {
    const q = chatInput.trim()
    if (!q || !selectedCid || chatRunning) return
    setChatRunning(true)
    setChatInput('')
    setChatMessages((xs) => [...xs, { role: 'user', content: q }])
    const currentFile = versions.find((v) => v.kind === 'masked')?.file || versions.find((v) => v.kind === 'redacted')?.file || ''
    try {
      const resp = await fetch(`/api/contracts/${selectedCid}/agent-chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, session_id: chatSessionId, file: currentFile, rule_files: selectedRuleFiles }),
      })
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n\n')
        buf = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          try {
            const evt = JSON.parse(line.slice(5).trim())
            if (evt.type === 'session_start') setChatSessionId(evt.session_id)
            else if (evt.type === 'agent_think') setChatMessages((xs) => [...xs, { role: 'agent_think', content: (evt.text || '').slice(0, 200) }])
            else if (evt.type === 'agent_tool_call') setChatMessages((xs) => [...xs, { role: 'agent_tool', content: '调用工具：' + evt.tool }])
            else if (evt.type === 'agent_tool_result') setChatMessages((xs) => [...xs, { role: 'agent_tool', content: (evt.summary || '').slice(0, 160) }])
            else if (evt.type === 'agent_retry') setChatMessages((xs) => [...xs, { role: 'agent_tool', content: '重试：' + evt.reason }])
            else if (evt.type === 'agent_report') setChatMessages((xs) => [...xs, { role: 'assistant', content: evt.report || evt.answer || '' }])
            else if (evt.type === 'final') setChatMessages((xs) => [...xs, { role: 'assistant', content: evt.answer || evt.report || '' }])
            else if (evt.type === 'error') setChatMessages((xs) => [...xs, { role: 'assistant', content: '⚠️ ' + evt.message }])
          } catch (e) { /* ignore */ }
        }
      }
    } catch (e) {
      setChatMessages((xs) => [...xs, { role: 'assistant', content: '请求失败: ' + e.message }])
    } finally {
      setChatRunning(false)
      loadVersions(selectedCid)
      loadMapping(selectedCid)
      loadContracts()
    }
  }

  const currentFile = versions.find((v) => v.kind === 'masked')?.file || versions.find((v) => v.kind === 'redacted')?.file || ''
  const scanList = [...scanItems, ...manualItems]

  const maskTab = (
    <Space direction="vertical" style={{ width: '100%' }} size={6}>
      <Text strong>脱敏项目</Text>
      <Checkbox.Group
        options={MASK_CATEGORIES}
        value={maskCategories}
        onChange={(v) => setMaskCategories(v)}
        style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}
      />
      <Text strong>脱敏方式</Text>
      <Radio.Group
        options={MASK_METHODS}
        value={maskMethod}
        onChange={(e) => setMaskMethod(e.target.value)}
        optionType="button"
        size="small"
      />
      <Space wrap>
        <Button size="small" type="primary" icon={<ScanOutlined />} loading={scanning} onClick={scanPii}>扫描待脱敏项目</Button>
        <Select
          size="small"
          value={manualCategory}
          onChange={setManualCategory}
          style={{ width: 110 }}
          options={[{ value: 'manual', label: '手动片段' }, ...MASK_CATEGORIES]}
        />
        <Button size="small" disabled={!selectionText} onClick={addSelectionToMask}>加入选中片段</Button>
      </Space>
      {selectionText && <Text type="secondary" style={{ fontSize: 12 }}>已拖选：{selectionText.slice(0, 30)}{selectionText.length > 30 ? '…' : ''}</Text>}
      <Text strong>待脱敏清单（勾选生效）</Text>
      <div style={{ maxHeight: 200, overflow: 'auto', border: '1px solid #f0f0f0', padding: 6, borderRadius: 6 }}>
        {!scanList.length && <Text type="secondary" style={{ fontSize: 12 }}>点击「扫描待脱敏项目」或拖选原文片段加入清单</Text>}
        <Checkbox.Group value={selectedScanIds} onChange={setSelectedScanIds} style={{ width: '100%' }}>
          {scanList.map((it) => (
            <div key={it.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6, marginBottom: 2 }}>
              <Checkbox value={it.id}>
                <Tag color={it.category === 'manual' ? 'orange' : 'blue'}>{it.category_label || it.category}</Tag>
                <Text style={{ fontSize: 12 }}>{(it.value || '').slice(0, 24)}{(it.value || '').length > 24 ? '…' : ''}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}> → {it.preview || it.value}</Text>
              </Checkbox>
              {it.category === 'manual' && <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeManualItem(it)} />}
            </div>
          ))}
        </Checkbox.Group>
      </div>
      <Button type="primary" block loading={masking} onClick={confirmMask}>确认脱敏</Button>
      <Text strong>脱密映射配置</Text>
      <div style={{ maxHeight: 180, overflow: 'auto', border: '1px solid #f0f0f0', padding: 6, borderRadius: 6 }}>
        {!mappingEntries.length && <Text type="secondary" style={{ fontSize: 12 }}>确认脱敏后生成映射配置</Text>}
        <Checkbox.Group value={selectedMapIds} onChange={setSelectedMapIds} style={{ width: '100%' }}>
          {mappingEntries.map((e) => (
            <div key={e.id}>
              <Checkbox value={e.id}>
                <Tag color="purple">{e.category_label || e.category}</Tag>
                <Text style={{ fontSize: 12 }}>{e.original}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}> → {e.masked_value}</Text>
              </Checkbox>
            </div>
          ))}
        </Checkbox.Group>
      </div>
      <Button block loading={restoring} onClick={restoreSelected} disabled={!selectedMapIds.length}>按选中脱密映射配置进行还原</Button>
      {restorePreview != null && (
        <pre style={{ maxHeight: 200, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12, background: '#fafafa', padding: 6, borderRadius: 6 }}>{restorePreview}</pre>
      )}
    </Space>
  )

  const reviewTab = (
    <Space direction="vertical" style={{ width: '100%' }} size={6}>
      <div style={{ height: 280, overflow: 'auto', border: '1px solid #f0f0f0', padding: 8, borderRadius: 6, background: '#fafafa' }}>
        {!chatMessages.length && <Text type="secondary" style={{ fontSize: 12 }}>向合同审查 agent 提问，例如：请审查这份施工合同并生成报告。</Text>}
        {chatMessages.map((m, i) => (
          <div key={i} style={{ marginBottom: 8 }}>
            {m.role === 'user' ? (
              <Tag color="blue">用户</Tag>
            ) : m.role === 'assistant' ? (
              <Tag color="green">助手</Tag>
            ) : (
              <Tag color="default">{m.role === 'agent_tool' ? '工具' : '推理'}</Tag>
            )}
            <Markdown content={m.content} style={{ fontSize: 12, display: 'inline-block', maxWidth: '100%' }} />
          </div>
        ))}
        {chatRunning && <Text type="secondary" style={{ fontSize: 12 }}>ReAct agent 执行中…</Text>}
      </div>
      <Input.TextArea
        value={chatInput}
        onChange={(e) => setChatInput(e.target.value)}
        placeholder="请输入审查指令，例如：请按基本流程审查当前脱敏合同"
        autoSize={{ minRows: 2, maxRows: 4 }}
        disabled={chatRunning}
      />
      <Space wrap>
        <Button type="primary" icon={<SendOutlined />} loading={chatRunning} onClick={sendChat}>发送</Button>
        <Button icon={<FolderOpenOutlined />} onClick={() => { setRuleModalSelected(selectedRuleFiles); setRuleModalOpen(true) }}>规则库</Button>
      </Space>
      <Space wrap size={4}>
        {currentFile && <Tag color="blue">脱敏文件：{currentFile}</Tag>}
        {selectedRuleFiles.map((f) => <Tag key={f} color="purple">{f}</Tag>)}
      </Space>
    </Space>
  )

  const artifactsTab = (
    <Space direction="vertical" style={{ width: '100%' }} size={6}>
      <Text type="secondary" style={{ fontSize: 12 }}>合同全生命周期产物，点击预览或下载</Text>
      <List
        size="small"
        dataSource={versions}
        renderItem={(v) => (
          <List.Item
            actions={[
              <Button key="pv" size="small" type="link" onClick={() => { setVersionKey(v.kind + '::' + v.file); loadContent(v.kind, v.file) }}>预览</Button>,
              <Button key="dl" size="small" type="link" href={`/api/contracts/${selectedCid}/download?kind=${v.kind}&file=${encodeURIComponent(v.file)}`} target="_blank">下载</Button>,
            ]}
          >
            <Tag color={v.kind === 'original' ? 'green' : v.kind === 'masked' ? 'blue' : v.kind === 'annotated' ? 'orange' : v.kind === 'report' ? 'red' : 'default'}>{v.label}</Tag>
            <Text style={{ fontSize: 12 }}>{v.file}</Text>
          </List.Item>
        )}
      />
    </Space>
  )

  const uploadMenu = {
    items: [
      { key: 'file', label: '上传文件' },
      { key: 'folder', label: '上传文件夹' },
    ],
    onClick: ({ key }) => {
      if (key === 'file') fileInputRef.current?.click()
      if (key === 'folder') folderInputRef.current?.click()
    },
  }

  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'stretch', minHeight: 'calc(100vh - 170px)' }}>
      {/* 左折叠按钮 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, justifyContent: 'flex-start' }}>
        <Button size="small" type="text" icon={leftCollapsed ? <RightOutlined /> : <LeftOutlined />} onClick={() => setLeftCollapsed(!leftCollapsed)} />
      </div>

      {/* 左侧：文档列表 */}
      {!leftCollapsed && (
        <Card size="small" title="文档列表" style={{ width: 240, flexShrink: 0 }} styles={{ body: { padding: 8 } }}
          extra={
            <Dropdown menu={uploadMenu} trigger={['click']}>
              <Button size="small" type="primary" icon={<UploadOutlined />}>添加</Button>
            </Dropdown>
          }>
          <Space direction="vertical" style={{ width: '100%' }} size={6}>
            <List
              size="small"
              dataSource={contracts}
              locale={{ emptyText: '暂无文档，点「添加」上传' }}
              renderItem={(c) => (
                <List.Item
                  style={{ cursor: 'pointer', background: c.contract_id === selectedCid ? '#e6f4ff' : undefined, padding: '6px 8px', borderRadius: 6, marginBottom: 2 }}
                  onClick={() => { setSelectedCid(c.contract_id); setSelected(c) }}
                  actions={[
                    <Button key="rn" size="small" type="text" icon={<EditOutlined />} onClick={(e) => { e.stopPropagation(); setRenameTarget(c); setRenameValue(c.original_name) }} />,
                    <Button key="dl" size="small" type="text" danger icon={<DeleteOutlined />} onClick={(e) => { e.stopPropagation(); removeContract(c) }} />,
                  ]}
                >
                  <Space direction="vertical" size={0} style={{ width: '100%' }}>
                    <Text style={{ fontSize: 13, wordBreak: 'break-all' }}>{c.original_name}</Text>
                    <Space size={4}>
                      <Tag color={c.status === 'reviewed' ? 'green' : c.status === 'masked' ? 'blue' : 'default'}>{c.status}</Tag>
                      {c.risk_count > 0 && <Text type="secondary" style={{ fontSize: 12 }}>{c.risk_count} 处风险</Text>}
                    </Space>
                  </Space>
                </List.Item>
              )}
            />
          </Space>
        </Card>
      )}

      {/* 中间：预览 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
        <Card size="small" title={selected ? `预览：${selected.original_name}` : '请选择文档'} loading={previewLoading}
          style={{ flex: 1, overflow: 'auto' }}
          styles={{ body: { height: '100%', overflow: 'auto' } }}>
          {previewContent ? (
            <pre
              onMouseUp={captureSelection}
              style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13, lineHeight: 1.7, margin: 0, userSelect: 'text' }}
            >{previewContent}</pre>
          ) : <Empty description="选择文档后加载内容" />}
        </Card>
        <Card size="small" styles={{ body: { padding: 8 } }}>
          <Space wrap>
            <Text strong>版本：</Text>
            <Select
              size="small"
              style={{ minWidth: 360, maxWidth: 560 }}
              value={versionKey}
              onChange={(v) => {
                setVersionKey(v)
                const [kind, file] = v.split('::')
                loadContent(kind, file)
              }}
              options={versions.map((v) => ({ value: v.kind + '::' + v.file, label: `${v.label} · ${v.file}` }))}
              placeholder="选择版本或报告"
            />
            <Button
              size="small"
              icon={<FileTextOutlined />}
              href={`/api/contracts/${selectedCid}/download?kind=${versionKey.split('::')[0] || 'redacted'}&file=${encodeURIComponent(versionKey.split('::')[1] || '')}`}
              target="_blank"
              disabled={!selectedCid || !versionKey}
            >下载当前版本</Button>
          </Space>
        </Card>
      </div>

      {/* 右折叠按钮 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, justifyContent: 'flex-start' }}>
        <Button size="small" type="text" icon={rightCollapsed ? <LeftOutlined /> : <RightOutlined />} onClick={() => setRightCollapsed(!rightCollapsed)} />
      </div>

      {/* 右侧：多 tab 工具栏 */}
      {!rightCollapsed && (
        <Card size="small" title="工具栏" style={{ width: 380, flexShrink: 0 }} styles={{ body: { padding: 8, height: '100%', overflow: 'auto' } }}>
          <Tabs
            activeKey={rightTab}
            onChange={setRightTab}
            size="small"
            items={[
              { key: 'mask', label: '脱敏', children: maskTab },
              { key: 'review', label: '审查', children: reviewTab },
              { key: 'artifacts', label: '文档产物', children: artifactsTab },
            ]}
          />
        </Card>
      )}

      {/* 隐藏的上传输入 */}
      <input ref={fileInputRef} type="file" accept=".docx,.pdf,.txt,.md" multiple style={{ display: 'none' }}
        onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />
      <input ref={folderInputRef} type="file" multiple style={{ display: 'none' }}
        onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />
      <input ref={ruleInputRef} type="file" accept=".txt,.md,.jsonl" multiple style={{ display: 'none' }}
        onChange={(e) => { uploadRuleFiles(e.target.files); e.target.value = '' }} />

      {/* 规则库弹出菜单 */}
      <Modal
        open={ruleModalOpen}
        title="规则库"
        okText="确定引用"
        cancelText="取消"
        onCancel={() => setRuleModalOpen(false)}
        onOk={() => { setSelectedRuleFiles(ruleModalSelected); setRuleModalOpen(false) }}
        width={560}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Button icon={<UploadOutlined />} onClick={() => ruleInputRef.current?.click()}>上传新的规则库（txt/md/jsonl，可多选）</Button>
          <div style={{ maxHeight: 320, overflow: 'auto' }}>
            <Checkbox.Group value={ruleModalSelected} onChange={setRuleModalSelected} style={{ width: '100%' }}>
              {ruleLibrary.length === 0 && <Text type="secondary" style={{ fontSize: 12 }}>暂无规则库文件</Text>}
              {ruleLibrary.map((f) => (
                <div key={f}>
                  <Checkbox value={f}><FileTextOutlined /> {f}</Checkbox>
                </div>
              ))}
            </Checkbox.Group>
          </div>
        </Space>
      </Modal>

      {/* 重命名弹窗 */}
      <Modal
        open={!!renameTarget}
        title="重命名文档"
        okText="保存"
        cancelText="取消"
        confirmLoading={renaming}
        onCancel={() => setRenameTarget(null)}
        onOk={submitRename}
      >
        <Input value={renameValue} onChange={(e) => setRenameValue(e.target.value)} placeholder="输入文档名称" />
      </Modal>
    </div>
  )
}

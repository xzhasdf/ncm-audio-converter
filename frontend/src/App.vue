<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { ConvertResponse, ConvertRow, TargetFormat } from './types'

interface UploadForm {
  target_format: TargetFormat
  bitrate: string
  workers: number
  overwrite: boolean
  flac_fallback: boolean
  output_mode: 'download' | 'folder'
  output_dir: string
  input_dir: string
}

const form = reactive<UploadForm>({
  target_format: 'flac',
  bitrate: 'lossless',
  workers: 3,
  overwrite: false,
  flac_fallback: true,
  output_mode: 'folder',
  output_dir: '',
  input_dir: '',
})

const running = ref(false)
const status = ref('等待操作')
const summary = ref<ConvertResponse | null>(null)
const rows = ref<ConvertRow[]>([])
const selectedFiles = ref<File[]>([])
const sourceMode = ref<'files' | 'folder'>('folder')
const downloadUrl = ref('')
const downloadToken = ref('')
const zipName = ref('converted_result.zip')

const formats: TargetFormat[] = ['mp3', 'wav', 'flac', 'm4a', 'ogg']
const bitrateOptions = ['128k', '192k', '256k', '320k', 'lossless(无损)']
const extensionOptions = ['.ncm', '.mp3', '.flac', '.wav', '.m4a', '.aac', '.ogg', '.wma']
const selectedExtensions = ref<string[]>(['.aac', '.flac', '.m4a', '.mp3', '.ncm', '.ogg', '.wav', '.wma'])

async function parseJsonSafe(resp: Response): Promise<unknown> {
  const raw = await resp.text()
  if (!raw.trim()) {
    return {}
  }
  try {
    return JSON.parse(raw) as Record<string, unknown>
  } catch {
    return { error: `服务返回了非 JSON 响应（HTTP ${resp.status}）` }
  }
}

function parseExtensionsSet(values: string[]): Set<string> {
  return new Set(values.map((v) => v.toLowerCase()))
}

function onFilesPick(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFiles.value = Array.from(input.files ?? [])
  sourceMode.value = 'files'
  form.input_dir = ''
  status.value = selectedFiles.value.length ? `文件模式: 已选择 ${selectedFiles.value.length} 个文件` : '未选择文件'
}

async function pickInputDir() {
  sourceMode.value = 'folder'
  selectedFiles.value = []
  try {
    const resp = await fetch('/api/pick-directory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: '请选择输入目录（仅扫描根目录）' }),
    })
    const data = (await parseJsonSafe(resp)) as { path?: string; cancelled?: boolean; error?: string }
    if (!resp.ok) {
      throw new Error(data.error || `目录选择失败（HTTP ${resp.status}）`)
    }
    if (!data.cancelled && data.path) {
      form.input_dir = data.path
      if (form.output_mode === 'folder' && !form.output_dir) {
        form.output_dir = data.path
      }
      status.value = `文件夹模式: 已选择 ${data.path}`
    }
  } catch (err) {
    status.value = `失败: ${(err as Error).message}`
  }
}

async function pickOutputDir() {
  try {
    const resp = await fetch('/api/pick-directory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: '请选择输出目录' }),
    })
    const data = (await parseJsonSafe(resp)) as { path?: string; cancelled?: boolean; error?: string }
    if (!resp.ok) {
      throw new Error(data.error || `目录选择失败（HTTP ${resp.status}）`)
    }
    if (!data.cancelled && data.path) {
      form.output_dir = data.path
    }
  } catch (err) {
    status.value = `失败: ${(err as Error).message}`
  }
}

function isDesktopApp(): boolean {
  return typeof (window as Window & { pywebview?: unknown }).pywebview !== 'undefined'
}

function supportsLossless(format: TargetFormat): boolean {
  return format === 'flac' || format === 'wav' || format === 'm4a'
}

const canUseLossless = computed(() => supportsLossless(form.target_format))
const canShowDownload = computed(() => !!downloadUrl.value && (summary.value?.success ?? 0) > 0)
const downloadHref = computed(() => {
  if (!downloadUrl.value) {
    return ''
  }
  const name = zipName.value.trim()
  if (!name) {
    return downloadUrl.value
  }
  const sep = downloadUrl.value.includes('?') ? '&' : '?'
  return `${downloadUrl.value}${sep}filename=${encodeURIComponent(name)}`
})

async function saveDownloadToFolder() {
  if (!downloadToken.value) {
    status.value = '没有可保存的下载文件'
    return
  }
  try {
    const pickResp = await fetch('/api/pick-directory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: '请选择保存 zip 的目录' }),
    })
    const pickData = (await parseJsonSafe(pickResp)) as { path?: string; cancelled?: boolean; error?: string }
    if (!pickResp.ok) {
      throw new Error(pickData.error || `目录选择失败（HTTP ${pickResp.status}）`)
    }
    if (pickData.cancelled || !pickData.path) {
      return
    }

    const saveResp = await fetch('/api/save-download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: downloadToken.value, output_dir: pickData.path, filename: zipName.value }),
    })
    const saveData = (await parseJsonSafe(saveResp)) as { saved_path?: string; error?: string }
    if (!saveResp.ok) {
      throw new Error(saveData.error || `保存失败（HTTP ${saveResp.status}）`)
    }
    status.value = `已保存: ${saveData.saved_path || pickData.path}`
  } catch (err) {
    status.value = `失败: ${(err as Error).message}`
  }
}

watch(
  () => form.output_mode,
  (mode) => {
    if (mode === 'folder' && sourceMode.value === 'folder' && form.input_dir && !form.output_dir) {
      form.output_dir = form.input_dir
    }
  },
)

watch(
  () => form.target_format,
  (fmt) => {
    form.bitrate = supportsLossless(fmt) ? 'lossless' : '320k'
  },
)

function filterUploadFiles(files: File[]): File[] {
  const extSet = parseExtensionsSet(selectedExtensions.value)
  return files.filter((file) => {
    const ext = `.${file.name.split('.').pop()?.toLowerCase() || ''}`
    return extSet.has(ext)
  })
}

async function runConvert() {
  if (!selectedExtensions.value.length) {
    status.value = '请至少选择一个扫描扩展名'
    return
  }
  if (sourceMode.value === 'files' && !selectedFiles.value.length) {
    status.value = '请先选择文件'
    return
  }
  if (sourceMode.value === 'folder' && !form.input_dir.trim()) {
    status.value = '请先选择输入目录'
    return
  }
  if (form.output_mode === 'folder' && sourceMode.value === 'folder' && !form.output_dir.trim()) {
    status.value = '请选择输出目录'
    return
  }
  if (form.output_mode === 'folder' && sourceMode.value === 'files' && !form.output_dir.trim()) {
    status.value = '文件上传模式请先选择输出目录（浏览器无法获取原文件路径）'
    return
  }

  running.value = true
  status.value = '正在处理，请稍候...'
  downloadUrl.value = ''
  downloadToken.value = ''
  summary.value = null
  rows.value = []

  try {
    if (sourceMode.value === 'folder') {
      const resp = await fetch('/api/convert-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          input_dir: form.input_dir,
          target_format: form.target_format,
          bitrate: form.bitrate,
          workers: form.workers,
          overwrite: form.overwrite,
          flac_fallback: form.flac_fallback,
          extensions: selectedExtensions.value.join(','),
          output_mode: form.output_mode,
          output_dir: form.output_dir,
          non_recursive: true,
        }),
      })
      const data = (await parseJsonSafe(resp)) as ConvertResponse & { error?: string; download_url?: string; output_dir?: string }
      if (!resp.ok) {
        throw new Error(data.error || `请求失败（HTTP ${resp.status}）`)
      }
      summary.value = data
      rows.value = data.results || []
      downloadUrl.value = data.download_url || ''
      downloadToken.value = (data.download_url || '').split('/').pop() || ''
      status.value =
        form.output_mode === 'folder' ? `转码完成，已输出到: ${data.output_dir || form.output_dir}` : '转码完成'
      return
    }

    const uploadFiles = filterUploadFiles(selectedFiles.value)
    if (!uploadFiles.length) {
      status.value = '没有匹配扩展名的文件'
      return
    }

    const body = new FormData()
    body.append('target_format', form.target_format)
    body.append('bitrate', form.bitrate)
    body.append('workers', String(form.workers))
    body.append('overwrite', String(form.overwrite))
    body.append('flac_fallback', String(form.flac_fallback))
    body.append('extensions', selectedExtensions.value.join(','))
    body.append('output_mode', form.output_mode)
    body.append('output_dir', form.output_dir)

    for (const file of uploadFiles) {
      body.append('files', file, file.name)
      body.append('relpaths', file.name)
      const withPath = file as File & { path?: string }
      body.append('source_paths', withPath.path || '')
    }

    const resp = await fetch('/api/upload-convert', {
      method: 'POST',
      body,
    })

    const data = (await parseJsonSafe(resp)) as ConvertResponse & { error?: string; download_url?: string; output_dir?: string }
    if (!resp.ok) {
      throw new Error(data.error || `请求失败（HTTP ${resp.status}）`)
    }

    summary.value = data
    rows.value = data.results || []
    downloadUrl.value = data.download_url || ''
    downloadToken.value = (data.download_url || '').split('/').pop() || ''
    status.value = form.output_mode === 'folder' ? `转码完成，已输出到: ${data.output_dir || form.output_dir}` : '转码完成'
  } catch (err) {
    status.value = `失败: ${(err as Error).message}`
  } finally {
    running.value = false
  }
}
</script>

<template>
  <section class="hero">
    <h1>网易云歌曲批量转码</h1>
    <p>文件夹模式只选择目录路径，开始转码时再扫描根目录文件，不读取子文件夹。</p>
  </section>

  <section class="card">
    <form class="grid" @submit.prevent="runConvert">
      <div class="full output-mode">
        <label><input v-model="sourceMode" type="radio" value="folder" /> 文件夹模式（推荐）</label>
        <label><input v-model="sourceMode" type="radio" value="files" /> 文件模式（上传）</label>
      </div>

      <div v-if="sourceMode === 'files'" class="full">
        <label for="files_input">选择文件（多选）</label>
        <input id="files_input" type="file" multiple @change="onFilesPick" />
        <div class="selected-hint">{{ selectedFiles.length ? `已选 ${selectedFiles.length} 个文件` : '尚未选择文件' }}</div>
      </div>

      <div v-else class="full row">
        <button class="pick-btn" type="button" @click="pickInputDir">选择输入目录</button>
        <span class="status">{{ form.input_dir || '尚未选择输入目录' }}</span>
      </div>

      <div>
        <label>目标格式</label>
        <div class="format-group">
          <label v-for="fmt in formats" :key="fmt" class="format-item">
            <input v-model="form.target_format" type="radio" :value="fmt" />
            <span>{{ fmt }}</span>
          </label>
        </div>
      </div>

      <div>
        <label>比特率（有损格式）</label>
        <div class="bitrate-group">
          <label v-for="bitrate in bitrateOptions" :key="bitrate" class="bitrate-item">
            <input
              v-model="form.bitrate"
              type="radio"
              :value="bitrate === 'lossless(无损)' ? 'lossless' : bitrate"
              :disabled="bitrate === 'lossless(无损)' && !canUseLossless"
            />
            <span>{{ bitrate }}</span>
          </label>
        </div>
        <div class="helper-text">无损建议搭配 flac / wav / m4a(无损)</div>
      </div>

      <div>
        <label for="workers">并发数</label>
        <div class="worker-slider">
          <input id="workers" v-model.number="form.workers" type="range" min="1" max="10" step="1" />
          <span>{{ form.workers }}</span>
        </div>
      </div>

      <div>
        <label>扫描扩展名（多选框）</label>
        <div class="ext-grid">
          <label v-for="ext in extensionOptions" :key="ext" class="ext-item">
            <input v-model="selectedExtensions" type="checkbox" :value="ext" />
            <span>{{ ext }}</span>
          </label>
        </div>
      </div>

      <div class="full output-mode">
        <label><input v-model="form.output_mode" type="radio" value="download" /> 结果直接下载(zip)</label>
        <label><input v-model="form.output_mode" type="radio" value="folder" /> 选择输出文件夹自动输出</label>
      </div>

      <div v-if="form.output_mode === 'download'" class="full">
        <label for="zip_name">下载文件名（zip）</label>
        <input id="zip_name" v-model="zipName" placeholder="例如 my-converted.zip" />
      </div>

      <div v-if="form.output_mode === 'folder'" class="full row">
        <button class="pick-btn" type="button" @click="pickOutputDir">选择输出目录</button>
        <span class="status">{{ form.output_dir || '尚未选择输出目录（文件夹模式默认等于输入目录）' }}</span>
      </div>

      <div class="full row">
        <input id="overwrite" v-model="form.overwrite" class="checkbox" type="checkbox" />
        <label for="overwrite" style="margin: 0">覆盖同名输出文件</label>
      </div>

      <div class="full row">
        <input id="flac_fallback" v-model="form.flac_fallback" class="checkbox" type="checkbox" />
        <label for="flac_fallback" style="margin: 0">FLAC 失败自动降级：WAV > WMA(无损) > MP3</label>
      </div>

      <div class="full row">
        <button class="btn" :disabled="running" type="submit">{{ running ? '处理中...' : '开始转码' }}</button>
        <button v-if="canShowDownload && isDesktopApp()" class="pick-btn" type="button" @click="saveDownloadToFolder">
          保存转换结果 (zip)
        </button>
        <a v-else-if="canShowDownload" class="download" :href="downloadHref">下载转换结果 (zip)</a>
        <span class="status">{{ status }}</span>
      </div>
    </form>
  </section>

  <section class="card">
    <div class="badges" v-if="summary">
      <span class="badge">扫描文件: {{ summary.total }}</span>
      <span class="badge">成功: {{ summary.success }}</span>
      <span class="badge">失败: {{ summary.failed }}</span>
    </div>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>状态</th>
            <th>源文件</th>
            <th>目标文件</th>
            <th>信息</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rows.length">
            <td colspan="4" style="color: #4f6671">暂无结果</td>
          </tr>
          <tr v-for="row in rows" :key="`${row.src}->${row.dst}`">
            <td :class="row.success ? 'ok' : 'fail'">{{ row.success ? 'OK' : 'FAIL' }}</td>
            <td>{{ row.src }}</td>
            <td>{{ row.dst }}</td>
            <td>{{ row.message }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

export type TargetFormat = 'mp3' | 'wav' | 'flac' | 'm4a' | 'ogg'

export interface ConvertRequest {
  input_dir: string
  output_dir: string
  target_format: TargetFormat
  bitrate: string
  workers: number
  overwrite: boolean
  extensions: string
}

export interface ConvertRow {
  src: string
  dst: string
  success: boolean
  message: string
}

export interface ConvertResponse {
  total: number
  success: number
  failed: number
  results: ConvertRow[]
  download_url?: string
  output_dir?: string
}

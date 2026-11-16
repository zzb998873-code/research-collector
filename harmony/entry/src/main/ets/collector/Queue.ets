import { fileIo as fs } from '@kit.CoreFileKit';
import { cryptoFramework } from '@kit.CryptoArchitectureKit';
import { util } from '@kit.ArkTS';
import { connection, http } from '@kit.NetworkKit';
import { workScheduler } from '@kit.BackgroundTasksKit';
import { BusinessError } from '@kit.BasicServicesKit';

const CHUNK: number = 1024 * 1024;
const WORK_ID: number = 804;

export interface Attachment {
  id: string;
  name: string;
  kind: string;
  size: number;
  sha256: string;
}

export interface Capture {
  version: number;
  id: string;
  captured_at: string;
  trigger: string;
  url: string;
  title: string;
  text: string;
  attachments: Attachment[];
  complete: boolean;
  category?: string;
  importance?: string;
  parent_id?: string;
}

export interface SharedFile {
  uri: string;
  name: string;
}

export interface SharedDraft {
  title: string;
  text: string;
  files: SharedFile[];
}

interface Pairing {
  version: number;
  url: string;
  token: string;
  ca: string;
}

interface Transfer {
  committed?: boolean;
  id?: string;
  offset?: number;
  offsets?: Record<string, number>;
  version?: number;
  error?: string;
  recent_captures?: boolean;
  items?: CaptureRecord[];
  record?: CaptureRecord;
  record_details?: boolean;
}

export interface CaptureRecord {
  id: string;
  title: string;
  date: string;
  status: string;
  source_id?: string;
  details?: string[];
  url?: string;
  category?: string;
  importance?: string;
  body?: string;
}

export function firstUrl(value: string): string {
  const matches: RegExpMatchArray | null = value.match(/https?:\/\/[^\s<>"，。！、；（）【】]+/gi);
  if (!matches || matches.length === 0) {
    return '';
  }
  const preferred: string | undefined = matches.find((entry: string): boolean => {
    return /^https?:\/\/(?:[^/.]+\.)*(?:xiaohongshu\.com|xhslink\.(?:cn|com)|douyin\.com|iesdouyin\.com)\//i.test(entry);
  });
  return (preferred ?? matches[0]).replace(/[).,;]+$/, '');
}

export function capturePreview(title: string, text: string): string {
  if (title.trim()) {
    return title.trim().slice(0, 160);
  }
  for (const line of text.split(/\r?\n/)) {
    const candidate: string = line.replace(/https?:\/\/[^\s<>"，。！、；（）【】]+/gi, '').trim();
    if (candidate) {
      return candidate.slice(0, 160);
    }
  }
  return firstUrl(text).slice(0, 160) || '图片或视频';
}

function exists(path: string): boolean {
  try {
    return fs.accessSync(path);
  } catch {
    return false;
  }
}

function ensureDir(path: string): void {
  if (!exists(path)) {
    try {
      fs.mkdirSync(path, true);
    } catch (error) {
      if (!exists(path)) {
        throw new Error((error as Error).message ?? '无法创建手机暂存目录');
      }
    }
  }
}

function saveText(path: string, text: string): void {
  const temp: string = path + '.' + util.generateRandomUUID() + '.tmp';
  const file: fs.File = fs.openSync(temp, fs.OpenMode.WRITE_ONLY | fs.OpenMode.CREATE);
  try {
    fs.writeSync(file.fd, text);
    fs.fsyncSync(file.fd);
  } finally {
    fs.closeSync(file);
  }
  fs.renameSync(temp, path);
}

function fileName(raw: string): string {
  const name: string = raw.split(/[\\/]/).pop() ?? '';
  try {
    return decodeURIComponent(name).replace(/[\\/:]/g, '-').slice(-150);
  } catch {
    return name.replace(/[\\/:]/g, '-').slice(-150);
  }
}

function kindFor(name: string): string {
  if (/\.(?:pdf|docx?)$/i.test(name)) {
    return 'document';
  }
  if (/\.(?:jpe?g|png|webp|gif|heic|heif|bmp)$/i.test(name)) {
    return 'image';
  }
  if (/\.(?:mp4|mov|webm|m4v)$/i.test(name)) {
    return 'video';
  }
  return '';
}

async function digest(path: string): Promise<string> {
  const md: cryptoFramework.Md = cryptoFramework.createMd('SHA256');
  const file: fs.File = fs.openSync(path, fs.OpenMode.READ_ONLY);
  try {
    const block: ArrayBuffer = new ArrayBuffer(CHUNK);
    let size: number = await fs.read(file.fd, block);
    while (size > 0) {
      await md.update({ data: new Uint8Array(block, 0, size) });
      size = await fs.read(file.fd, block);
    }
    const hash: cryptoFramework.DataBlob = await md.digest();
    let result: string = '';
    hash.data.forEach((b: number): void => {
      result += b.toString(16).padStart(2, '0');
    });
    return result;
  } finally {
    fs.closeSync(file);
  }
}

function queued(root: string): string[] {
  const folder: string = root + '/captures';
  return exists(folder) ? fs.listFileSync(folder).filter((id: string): boolean => {
    return /^[a-f0-9-]{36}$/.test(id) && exists(folder + '/' + id + '/manifest.json');
  }).sort() : [];
}

export function queueSize(root: string): number {
  return queued(root).length;
}

export function captureRecords(root: string): CaptureRecord[] {
  const records: CaptureRecord[] = [];
  for (const id of queued(root).slice(0, 30)) {
    try {
      const capture: Capture = JSON.parse(fs.readTextSync(root + '/captures/' + id + '/manifest.json')) as Capture;
      records.push({ id: id, title: capturePreview(capture.title, capture.text),
        url: capture.url, category: capture.category, importance: capture.importance,
        date: new Date(Date.parse(capture.captured_at) + 8 * 60 * 60 * 1000).toISOString().slice(0, 10),
        status: 'pending' });
    } catch {
      // The background upload may have just removed the manifest.
    }
  }
  records.sort((a: CaptureRecord, b: CaptureRecord): number => b.date.localeCompare(a.date));
  try {
    const cached: Transfer = JSON.parse(fs.readTextSync(root + '/recent.json')) as Transfer;
    return records.concat(cached.items ?? []);
  } catch {
    return records;
  }
}

export function lastCategory(root: string): string {
  try {
    return fs.readTextSync(root + '/last-category.txt');
  } catch {
    return '';
  }
}

export function paired(root: string): boolean {
  return exists(root + '/pairing.json');
}

export async function addCapture(root: string, trigger: string, title: string, text: string,
  url: string, files: SharedFile[], complete: boolean = false, category: string = '',
  importance: string = '', parentId: string = ''): Promise<void> {
  if (!text.trim() && !url && files.length === 0) {
    throw new Error('请提供链接、文字、图片或视频');
  }
  if (trigger === 'supplement' && !url && !parentId) {
    throw new Error('补传附件时请填写原帖链接');
  }
  if (complete && (!url || !text.replace(url, '').trim() || files.length === 0)) {
    throw new Error('请同时提供原帖链接、完整正文和全部附件');
  }
  if (text.length > 90000 || title.length > 500 || url.length > 4000 || files.length > 64) {
    throw new Error('分享文字或附件数量超过收集上限');
  }
  const capture: Capture = {
    version: 1, id: util.generateRandomUUID().toLowerCase(), captured_at: new Date().toISOString(),
    trigger: trigger, url: url, title: title, text: text, attachments: [], complete: complete
  };
  if (category) {
    capture.category = category;
  }
  if (importance) {
    capture.importance = importance;
  }
  if (parentId) {
    capture.parent_id = parentId;
  }
  const folder: string = root + '/captures/' + capture.id;
  ensureDir(folder);
  try {
    let total: number = 0;
    for (const item of files) {
      const name: string = fileName(item.name);
      const kind: string = kindFor(name);
      if (!kind || !item.uri.startsWith('file://')) {
        throw new Error('分享来源未提供可读取的图片、视频或 PDF / Word 文件');
      }
      const id: string = util.generateRandomUUID().toLowerCase();
      const dest: string = folder + '/' + id + '.media';
      const source: fs.File = fs.openSync(item.uri, fs.OpenMode.READ_ONLY);
      let size: number = 0;
      try {
        size = fs.statSync(source.fd).size;
        total += size;
        if (size <= 0 || size > 512 * CHUNK || total > 768 * CHUNK) {
          throw new Error('附件为空，或超过单文件 512 MiB／单次 768 MiB 的上限');
        }
        await fs.copyFile(source.fd, dest);
      } finally {
        fs.closeSync(source);
      }
      if (fs.statSync(dest).size !== size) {
        throw new Error('附件复制未完成，请重新分享');
      }
      const checksum: string = await digest(dest);
      capture.attachments.push({ id: id, name: name, kind: kind, size: size, sha256: checksum });
    }
    saveText(folder + '/manifest.json', JSON.stringify(capture));
  } catch (error) {
    fs.rmdirSync(folder);
    throw new Error((error as Error).message ?? '手机暂存失败');
  }
  if (category) {
    try {
      saveText(root + '/last-category.txt', category);
    } catch {
      // The capture is already in the queue.
    }
  }
}

function wifi(): boolean {
  try {
    const network: connection.NetHandle = connection.getDefaultNetSync();
    return connection.getNetCapabilitiesSync(network).bearerTypes.includes(connection.NetBearType.BEARER_WIFI);
  } catch {
    return false;
  }
}

export async function recordDetail(root: string, item: CaptureRecord): Promise<CaptureRecord> {
  if (!/^[a-f0-9-]{36}$/.test(item.id)) {
    throw new Error('记录编号无效');
  }
  const manifest: string = root + '/captures/' + item.id + '/manifest.json';
  if (exists(manifest)) {
    const capture: Capture = JSON.parse(fs.readTextSync(manifest)) as Capture;
    return { id: item.id, title: item.title, date: item.date, status: 'pending', url: capture.url,
      category: capture.category, importance: capture.importance, body: '待传输\n\n' + capture.text };
  }
  const cache: string = root + '/detail-' + item.id + '.json';
  try {
    if (!paired(root) || !wifi()) {
      throw new Error('请连接电脑以读取完整详情');
    }
    const pairing: Pairing = JSON.parse(fs.readTextSync(root + '/pairing.json')) as Pairing;
    const result: Transfer = await request(root, pairing, http.RequestMethod.GET, '/v1/captures/' + item.id + '/detail');
    if (!result.record) {
      throw new Error('电脑未返回详情');
    }
    saveText(cache, JSON.stringify(result.record));
    return result.record;
  } catch (error) {
    if (exists(cache)) {
      const record: CaptureRecord = JSON.parse(fs.readTextSync(cache)) as CaptureRecord;
      record.body = '离线缓存 · 连接电脑后刷新\n\n' + (record.body ?? '');
      return record;
    }
    throw new Error(connectionError(error as BusinessError));
  }
}

export async function duplicateRecord(root: string, url: string): Promise<CaptureRecord | undefined> {
  if (!url) {
    return undefined;
  }
  const cached: CaptureRecord | undefined = captureRecords(root).find((item: CaptureRecord): boolean => item.url === url);
  if (cached || !paired(root) || !wifi()) {
    return cached;
  }
  const pairing: Pairing = JSON.parse(fs.readTextSync(root + '/pairing.json')) as Pairing;
  const health: Transfer = await request(root, pairing, http.RequestMethod.GET, '/v1/health');
  if (!health.record_details) {
    return undefined;
  }
  const result: Transfer = await request(root, pairing, http.RequestMethod.POST, '/v1/lookup', JSON.stringify({ url: url }));
  return result.record ?? undefined;
}

export async function retryRecord(root: string, id: string): Promise<void> {
  if (!/^[a-f0-9-]{36}$/.test(id) || !paired(root) || !wifi()) {
    throw new Error('请连接电脑后重试处理');
  }
  const pairing: Pairing = JSON.parse(fs.readTextSync(root + '/pairing.json')) as Pairing;
  await request(root, pairing, http.RequestMethod.POST, '/v1/captures/' + id + '/retry', '{}');
}

function certificatePath(root: string, pairing: Pairing): string {
  const address: string = pairing.url.substring(8).replace(/[.:]/g, '-');
  return root + '/receiver-' + address + '-' + pairing.token.slice(0, 16) + '.pem';
}

async function request(root: string, pairing: Pairing, method: http.RequestMethod, path: string,
  payload?: string | ArrayBuffer, offset?: number): Promise<Transfer> {
  const client: http.HttpRequest = http.createHttp();
  const headers: Record<string, string> = {
    'Authorization': 'Bearer ' + pairing.token,
    'Content-Type': typeof payload === 'string' ? 'application/json' : 'application/octet-stream'
  };
  if (offset !== undefined) {
    headers['Upload-Offset'] = offset.toString();
  }
  try {
    const response: http.HttpResponse = await client.request(pairing.url + path, {
      method: method, header: headers, extraData: payload,
      caPath: certificatePath(root, pairing), usingCache: false, usingProxy: false,
      connectTimeout: 4500, readTimeout: 15000, expectDataType: http.HttpDataType.STRING
    });
    if (response.responseCode === 401) {
      throw new Error('配对已失效，请使用电脑最新的配对文件重新配对');
    }
    const value: Transfer = JSON.parse(response.result as string) as Transfer;
    if (response.responseCode !== 200) {
      throw new Error(value.error ?? '电脑返回 ' + response.responseCode.toString());
    }
    return value;
  } finally {
    client.destroy();
  }
}

export function connectionError(error: BusinessError): string {
  switch (error.code) {
    case 2300028:
      return '连接电脑超时，请确认电脑已开机并与手机处于同一局域网';
    case 2300007:
      return '无法连接电脑，请确认接收端已启动、电脑与手机处于同一局域网';
    case 2300058:
    case 2300060:
    case 2300077:
    case 2300091:
      return '电脑证书校验失败，请使用电脑最新的配对文件重新配对';
    default:
      return error.code ? '连接中断，请检查电脑和 Wi-Fi 后重试' : error.message;
  }
}

export async function setPairing(root: string, input: string): Promise<void> {
  const pairing: Pairing = JSON.parse(input.trim()) as Pairing;
  if (pairing.version !== 1 || !/^https:\/\/(?:\d{1,3}\.){3}\d{1,3}:\d{4,5}$/.test(pairing.url)
    || !/^[A-Za-z0-9_-]{40,60}$/.test(pairing.token)
    || !/^-----BEGIN CERTIFICATE-----[\r\nA-Za-z0-9+/=]+-----END CERTIFICATE-----\s*$/.test(pairing.ca)) {
    throw new Error('请选择电脑生成的配对文件');
  }
  ensureDir(root);
  const staging: string = root + '/pair-' + util.generateRandomUUID();
  ensureDir(staging);
  try {
    saveText(certificatePath(staging, pairing), pairing.ca);
    if (!wifi()) {
      throw new Error('请先连接与电脑相同的 Wi-Fi');
    }
    const health: Transfer = await request(staging, pairing, http.RequestMethod.GET, '/v1/health');
    if (health.version !== 1) {
      throw new Error('电脑端版本不匹配');
    }
    saveText(certificatePath(root, pairing), pairing.ca);
    saveText(root + '/recent.json', '{"items":[]}');
    saveText(root + '/pairing.json', JSON.stringify(pairing));
  } finally {
    fs.rmdirSync(staging);
  }
}

async function transferCapture(root: string, pairing: Pairing, folder: string, end: number): Promise<boolean> {
  const manifest: string = fs.readTextSync(folder + '/manifest.json');
  const capture: Capture = JSON.parse(manifest) as Capture;
  const base: string = '/v1/captures/' + capture.id;
  const status: Transfer = await request(root, pairing, http.RequestMethod.POST, '/v1/captures', manifest);
  if (!status.committed) {
    for (const item of capture.attachments) {
      let offset: number = status.offsets?.[item.id] ?? 0;
      while (offset < item.size) {
        if (Date.now() >= end) {
          return false;
        }
        const buffer: ArrayBuffer = new ArrayBuffer(Math.min(CHUNK, item.size - offset));
        const file: fs.File = fs.openSync(folder + '/' + item.id + '.media', fs.OpenMode.READ_ONLY);
        let read: number;
        try {
          read = await fs.read(file.fd, buffer, { offset: offset, length: buffer.byteLength });
        } finally {
          fs.closeSync(file);
        }
        if (read <= 0) {
          throw new Error('手机暂存的文件不完整');
        }
        const response: Transfer = await request(root, pairing, http.RequestMethod.PUT,
          base + '/files/' + item.id, read === buffer.byteLength ? buffer : buffer.slice(0, read), offset);
        if (response.offset === undefined || response.offset === offset || response.offset < 0 ||
          response.offset > item.size) {
          throw new Error('电脑未确认上传进度');
        }
        offset = response.offset;
      }
    }
    const result: Transfer = await request(root, pairing, http.RequestMethod.POST, base + '/commit', '{}');
    if (!result.committed || result.id !== capture.id) {
      throw new Error('电脑未确认接收');
    }
  }
  if (exists(folder)) {
    fs.rmdirSync(folder);
  }
  return true;
}

let active: boolean = false;

export async function sync(root: string, budgetMs: number = 70000, refreshRecent: boolean = false): Promise<string> {
  if (active) {
    return '正在同步';
  }
  if (!paired(root)) {
    return '等待与电脑配对';
  }
  if (!wifi()) {
    return '手机未连接 Wi-Fi，请连接与电脑同一局域网的 Wi-Fi';
  }
  active = true;
  const end: number = Date.now() + budgetMs;
  try {
    const pairing: Pairing = JSON.parse(fs.readTextSync(root + '/pairing.json')) as Pairing;
    const health: Transfer = await request(root, pairing, http.RequestMethod.GET, '/v1/health');
    if (health.version !== 1) {
      throw new Error('电脑接收端版本不匹配');
    }
    let total: number = 0;
    for (const id of queued(root)) {
      if (Date.now() >= end || !await transferCapture(root, pairing, root + '/captures/' + id, end)) {
        return '已保存上传进度，剩余资料稍后补传';
      }
      total++;
    }
    if (refreshRecent && health.recent_captures) {
      const result: Transfer = await request(root, pairing, http.RequestMethod.GET, '/v1/captures/recent');
      if (!Array.isArray(result.items)) {
        throw new Error('电脑未返回处理记录，请稍后重试');
      }
      saveText(root + '/recent.json', JSON.stringify(result));
    } else if (refreshRecent) {
      return '已连接电脑，请更新电脑接收端以查看入库状态';
    }
    return total ? '电脑在线，已接收 ' + total.toString() + ' 条' : '电脑在线';
  } finally {
    active = false;
  }
}

export async function schedule(root: string, bundle: string): Promise<void> {
  try {
    const registered: workScheduler.WorkInfo[] = await workScheduler.obtainAllWorks();
    const work: workScheduler.WorkInfo | undefined = registered.find((item: workScheduler.WorkInfo): boolean => {
      return item.workId === WORK_ID;
    });
    if (queueSize(root) > 0 && paired(root)) {
      if (!work) {
        workScheduler.startWork({
          workId: WORK_ID, bundleName: bundle, abilityName: 'SyncWork',
          networkType: workScheduler.NetworkType.NETWORK_TYPE_WIFI,
          isRepeat: true, repeatCycleTime: 2 * 60 * 60 * 1000, isPersisted: true
        });
      }
    } else if (work) {
      workScheduler.stopWork(work, true);
    }
  } catch {
    // The durable queue is also drained when the app next opens.
  }
}

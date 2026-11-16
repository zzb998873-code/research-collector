import { common } from '@kit.AbilityKit';
import { BusinessError, pasteboard } from '@kit.BasicServicesKit';
import { picker, fileIo as fs } from '@kit.CoreFileKit';
import { photoAccessHelper } from '@kit.MediaLibraryKit';
import { CaptureRecord, SharedDraft, SharedFile, addCapture, capturePreview, captureRecords, connectionError,
  firstUrl, lastCategory, paired, queueSize, schedule, setPairing, sync } from '../collector/Queue';
import { recordDetail, duplicateRecord, retryRecord } from '../collector/Queue';

const NAVY: string = '#49362B';
const INK: string = '#49362B';
const MUTED: string = '#796253';
const SURFACE: string = '#F6EBD9';
const TINT: string = '#FFF6E6';
const BORDER: string = '#49362B';
const HONEY: string = '#F1BC69';
const PAPER: string = '#FFFCF4';
const TYPES: string[] = ['科研绘图', '写作润色', '学科进展', '配色', '数据处理', '自定义'];
const IMPORTANCE: string[] = ['低', '中', '高'];

@Entry
@Component
struct Index {
  @StorageLink('queueCount') queueCount: number = 0;
  @StorageLink('syncState') syncState: string = '';
  @StorageLink('captureState') captureState: string = '';
  @StorageLink('shareDraft') @Watch('readShareDraft') shareDraft: string = '';
  @StorageLink('foregroundCount') @Watch('resumePage') foregroundCount: number = 0;
  @State hasPairing: boolean = false;
  @State busy: boolean = false;
  @State @Watch('draftChanged') draft: string = '';
  @State files: SharedFile[] = [];
  @State complete: boolean = false;
  @State category: string = '';
  @State customCategory: string = '';
  @State importance: string = '中';
  @State errorText: string = '';
  @State clipboardHint: string = '';
  @State records: CaptureRecord[] = [];
  @State showRecords: boolean = false;
  @State syncing: boolean = false;
  @State title: string = '';
  @State selected: CaptureRecord | undefined = undefined;
  @State duplicate: CaptureRecord | undefined = undefined;
  @State parentId: string = '';
  @State drawingNotes: string = '';
  private parentUrl: string = '';
  private duplicateTimer: number = -1;
  private fromShare: boolean = false;
  private poll: number = -1;
  private scroller: Scroller = new Scroller();

  aboutToAppear(): void {
    const context: common.UIAbilityContext = this.context();
    this.hasPairing = paired(context.filesDir);
    this.refreshRecords();
    this.restoreType();
    this.readShareDraft();
  }

  onPageShow(): void {
    this.resumePage();
  }

  onPageHide(): void {
    clearInterval(this.poll);
    this.poll = -1;
  }

  aboutToDisappear(): void {
    clearTimeout(this.duplicateTimer);
    this.onPageHide();
  }

  private resumePage(): void {
    this.inspectClipboard();
    this.flush();
    if (this.poll < 0) {
      this.poll = setInterval((): void => {
        if (this.hasPairing) {
          this.flush(false);
        }
      }, 30000);
    }
  }

  private restoreType(): void {
    const previous: string = lastCategory(this.context().filesDir);
    const custom: boolean = !!previous && !TYPES.slice(0, -1).includes(previous);
    this.category = custom ? '自定义' : previous;
    this.customCategory = custom ? previous : '';
  }

  private refreshRecords(): void {
    const root: string = this.context().filesDir;
    this.queueCount = queueSize(root);
    this.records = captureRecords(root);
  }

  private async inspectClipboard(): Promise<void> {
    try {
      const board = pasteboard.getSystemPasteboard();
      const patterns = await board.detectPatterns([pasteboard.Pattern.URL]);
      this.clipboardHint = patterns.includes(pasteboard.Pattern.URL) ? '剪贴板中有链接，点粘贴载入' : '';
    } catch {
      this.clipboardHint = '';
    }
  }

  private readShareDraft(): void {
    if (!this.shareDraft || this.busy) {
      return;
    }
    const share: SharedDraft = JSON.parse(this.shareDraft) as SharedDraft;
    this.shareDraft = '';
    this.showRecords = false;
    this.selected = undefined;
    this.parentId = '';
    this.drawingNotes = '';
    this.draft = share.text;
    this.title = share.title;
    this.fromShare = true;
    this.files = share.files;
    this.complete = false;
    this.restoreType();
    this.importance = '中';
    this.errorText = '';
  }

  private context(): common.UIAbilityContext {
    return this.getUIContext().getHostContext() as common.UIAbilityContext;
  }

  private async readClipboard(): Promise<void> {
    try {
      const data: pasteboard.PasteData = await pasteboard.getSystemPasteboard().getData();
      const value: string = data.getPrimaryText() || firstUrl(data.getPrimaryUri() || '');
      if (!value.trim()) {
        this.errorText = '剪贴板暂无可读取的内容';
        return;
      }
      this.draft = value;
      this.parentId = '';
      this.drawingNotes = '';
      this.draftChanged();
      this.title = '';
      this.fromShare = false;
      this.errorText = '';
      this.captureState = '';
      this.clipboardHint = '';
    } catch (error) {
      this.errorText = '读取剪贴板失败，请再点一次粘贴';
    }
  }

  private async selectFiles(): Promise<void> {
    if (this.files.length >= 64) { return; }
    try {
      const options: photoAccessHelper.PhotoSelectOptions = new photoAccessHelper.PhotoSelectOptions();
      options.MIMEType = photoAccessHelper.PhotoViewMIMETypes.IMAGE_VIDEO_TYPE;
      options.maxSelectNumber = 64 - this.files.length;
      const result: photoAccessHelper.PhotoSelectResult =
        await new photoAccessHelper.PhotoViewPicker().select(options);
      if (result.photoUris.length) {
        this.files = this.files.concat(result.photoUris.map((uri: string): SharedFile => {
          return { uri: uri, name: uri.split('/').pop() ?? '' };
        }));
      }
    } catch (error) {
      this.captureState = '无法读取图库 · ' + (error as Error).message;
    }
  }

  private async selectDocuments(): Promise<void> {
    try {
      const options: picker.DocumentSelectOptions = new picker.DocumentSelectOptions();
      options.maxSelectNumber = Math.max(1, 64 - this.files.length);
      options.fileSuffixFilters = ['论文文件|.pdf,.doc,.docx'];
      const uris: string[] = await new picker.DocumentViewPicker(this.context()).select(options);
      this.files = this.files.concat(uris.map((uri: string): SharedFile => {
        return { uri: uri, name: uri.split('/').pop() ?? '' };
      }));
    } catch (error) {
      this.captureState = '无法读取文档 · ' + (error as Error).message;
    }
  }

  private async pair(): Promise<void> {
    if (this.busy) {
      return;
    }
    this.busy = true;
    try {
      const context: common.UIAbilityContext = this.context();
      const options: picker.DocumentSelectOptions = new picker.DocumentSelectOptions();
      options.maxSelectNumber = 1;
      options.fileSuffixFilters = ['配对文件|.json'];
      const files: string[] = await new picker.DocumentViewPicker(context).select(options);
      if (files.length === 0) {
        return;
      }
      const path: string = context.cacheDir + '/research-pairing.json';
      const file: fs.File = fs.openSync(files[0], fs.OpenMode.READ_ONLY);
      try {
        const length: number = fs.statSync(file.fd).size;
        if (length < 20 || length > 12000) {
          throw new Error('配对文件大小不正确');
        }
        fs.copyFileSync(file.fd, path);
        await setPairing(context.filesDir, fs.readTextSync(path));
      } finally {
        fs.closeSync(file);
        if (fs.accessSync(path)) {
          fs.unlinkSync(path);
        }
      }
      this.hasPairing = true;
      this.captureState = '已配对';
    } catch (error) {
      this.captureState = '配对失败 · ' + connectionError(error as BusinessError);
    } finally {
      this.busy = false;
    }
    if (this.hasPairing) {
      await this.flush();
    }
  }

  private async save(): Promise<void> {
    if (this.busy) {
      return;
    }
    if (this.duplicate && !this.parentId) {
      await this.openDetail(this.duplicate);
      return;
    }
    if (this.parentId && firstUrl(this.draft) !== this.parentUrl) {
      this.errorText = '补充资料请保留原帖链接；新作品请从剪贴板重新载入';
      return;
    }
    const category: string = this.category === '自定义' ? this.customCategory.trim() : this.category;
    if (!category) {
      this.errorText = this.category === '自定义' ? '请输入自定义类型' : '请选择资料类型';
      return;
    }
    this.busy = true;
    this.errorText = '';
    const root: string = this.context().filesDir;
    try {
      const url: string = firstUrl(this.draft);
      if (!this.parentId) {
        let match: CaptureRecord | undefined = undefined;
        try {
          match = await duplicateRecord(root, url);
        } catch {
          // The saved queue can be reconciled offline.
        }
        if (match) {
          this.busy = false;
          this.duplicate = match;
          await this.openDetail(match);
          return;
        }
      }
      const trigger: string = this.parentId || this.files.length > 0 && url ? 'supplement' : (this.fromShare ? 'share' : 'paste');
      const text: string = this.draft + (this.drawingNotes.trim() ? '\n\n用户补充绘图记录\n' + this.drawingNotes.trim() : '');
      await addCapture(root, trigger, this.title, text, url, this.files,
        this.complete && this.files.length > 0 && !!url, category, this.importance, this.parentId);
      this.parentId = '';
      this.drawingNotes = '';
      this.draft = '';
      this.files = [];
      this.complete = false;
      this.title = '';
      this.fromShare = false;
      this.restoreType();
      this.importance = '中';
      this.captureState = '已加入待传输队列';
      this.refreshRecords();
    } catch (error) {
      this.errorText = '保存失败 · ' + (error as Error).message;
      return;
    } finally {
      this.busy = false;
    }
    await this.flush();
  }

  private async flush(interactive: boolean = true): Promise<void> {
    if (this.syncing || this.busy) {
      return;
    }
    this.syncing = true;
    const context: common.UIAbilityContext = this.context();
    const root: string = context.filesDir;
    schedule(root, context.abilityInfo.bundleName);
    if (interactive) {
      this.syncState = '正在连接电脑…';
    }
    try {
      this.syncState = await sync(root, 70000, true);
    } catch (error) {
      this.syncState = connectionError(error as BusinessError);
    } finally {
      this.syncing = false;
      this.refreshRecords();
      schedule(root, context.abilityInfo.bundleName);
    }
  }

  private statusLabel(status: string): string {
    switch (status) {
      case 'pending': return '↑  待传输';
      case 'received': return '↓  电脑已接收';
      case 'processing': return '↻  处理中';
      case 'missing': return '!  待补资料';
      case 'archived': return '✓  已入库';
      default: return status;
    }
  }

  private draftChanged(): void {
    this.duplicate = undefined;
    clearTimeout(this.duplicateTimer);
    const url: string = firstUrl(this.draft);
    if (!url || this.parentId) {
      return;
    }
    this.duplicateTimer = setTimeout(async (): Promise<void> => {
      try {
        const match: CaptureRecord | undefined = await duplicateRecord(this.context().filesDir, url);
        if (firstUrl(this.draft) === url && !this.parentId) {
          this.duplicate = match;
        }
      } catch {
        // Offline captures are reconciled on the computer.
      }
    }, 700);
  }

  private async openDetail(item: CaptureRecord): Promise<void> {
    if (this.busy) {
      return;
    }
    this.busy = true;
    try {
      this.selected = await recordDetail(this.context().filesDir, item);
      this.showRecords = true;
      this.scroller.scrollTo({ xOffset: 0, yOffset: 0 });
    } catch (error) {
      this.captureState = connectionError(error as BusinessError);
    } finally {
      this.busy = false;
    }
  }

  private supplement(item: CaptureRecord): void {
    if (this.draft.trim() || this.files.length) {
      this.getUIContext().showAlertDialog({
        message: '载入原记录会替换当前未提交的草稿。',
        primaryButton: { value: '保留草稿', action: (): void => {} },
        secondaryButton: { value: '载入原记录', action: (): void => this.loadSupplement(item) }
      });
    } else {
      this.loadSupplement(item);
    }
  }

  private loadSupplement(item: CaptureRecord): void {
    this.parentId = item.id;
    this.parentUrl = item.url ?? '';
    this.title = item.title;
    this.fromShare = false;
    this.files = [];
    this.complete = false;
    const type: string = item.category ?? '';
    this.drawingNotes = '';
    this.category = TYPES.includes(type) ? type : '自定义';
    this.customCategory = this.category === '自定义' ? type : '';
    this.importance = item.importance ?? '中';
    this.draft = this.parentUrl || '补充资料';
    this.duplicate = undefined;
    this.selected = undefined;
    this.showRecords = false;
    this.captureState = '已关联原记录，可补充素材、正文或修改分类与重要程度';
  }

  private leaveSupplement(): void {
    if (this.busy) {
      return;
    }
    let content: string = this.draft;
    if (this.parentUrl && content.startsWith(this.parentUrl)) {
      content = content.slice(this.parentUrl.length).trimStart();
    } else if (!this.parentUrl && content === '补充资料') {
      content = '';
    }
    this.parentId = '';
    this.parentUrl = '';
    this.title = '';
    this.fromShare = false;
    this.complete = false;
    this.duplicate = undefined;
    this.draft = content;
    this.errorText = '';
    this.captureState = '已切换为新建收集，请粘贴新链接；补充文字、参数和附件已保留';
  }

  private statusColor(status: string, background: boolean = false): string {
    switch (status) {
      case 'received': return background ? '#EFF6FF' : '#1D4ED8';
      case 'processing': return background ? '#F5F3FF' : '#6D28D9';
      case 'missing': return background ? '#FFFBEB' : '#92400E';
      case 'archived': return background ? '#F0FDF4' : '#166534';
      default: return background ? '#F1F5F9' : '#475569';
    }
  }

  private connectionColor(): string {
    if (this.syncState.startsWith('电脑在线') || this.syncState.startsWith('已连接电脑')) {
      return '#15803D';
    }
    return this.syncState.startsWith('正在') ? '#B45309' : '#B91C1C';
  }

  @Styles raisedButton() {
    .border({ width: { left: 2, right: 2, top: 2, bottom: 6 }, color: INK, radius: 12 })
    .shadow({ radius: 1, color: '#6049362B', offsetX: 1, offsetY: 3 })
    .translate({ y: 0 })
  }

  @Styles pressedButton() {
    .border({ width: { left: 2, right: 2, top: 2, bottom: 2 }, color: INK, radius: 12 })
    .shadow({ radius: 1, color: '#2049362B', offsetX: 0, offsetY: 1 })
    .translate({ y: 3 })
  }

  @Builder option(value: string, isType: boolean) {
    Button(value, { type: ButtonType.Normal })
      .layoutWeight(1).height(48).borderRadius(9).fontSize(14)
      .backgroundColor((isType ? this.category : this.importance) === value ? HONEY : TINT)
      .border({ width: 2, color: BORDER }).shadow({ radius: 1, color: INK, offsetX: 2, offsetY: 4 })
      .fontColor(INK)
      .accessibilityDescription((isType ? this.category : this.importance) === value ? '已选' : '未选')
      .stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => {
        if (isType) {
          this.category = value;
        } else {
          this.importance = value;
        }
        this.errorText = '';
      })
  }

  private async openOriginal(url: string): Promise<void> {
    if (!/^https?:\/\//.test(url)) { return; }
    try {
      await this.context().openLink(url, { appLinkingOnly: true });
    } catch (_) {
      this.getUIContext().showAlertDialog({
        title: '暂时无法在原生 App 中打开',
        message: '请确认已安装对应 App。可复制下方原帖链接到 App 内打开。',
        primaryButton: { value: '取消', action: () => {} },
        secondaryButton: { value: '用浏览器打开', action: () => {
          this.context().openLink(url, { appLinkingOnly: false }).catch(() => {
            this.captureState = '无法打开链接，请复制链接后重试';
          });
        } }
      });
    }
  }

  private displayTitle(title: string): string {
    const match = title.match(/【[^】]+的作品】\s*(.+)/);
    const readable = match ? match[1] : title;
    return readable.replace(/https?:\/\/\S+.*$/, '').trim() || readable;
  }

  build() {
    Scroll(this.scroller) {
      Column({ space: 18 }) {
        Row({ space: 12 }) {
          Column({ space: 4 }) {
            Text('科研收集').fontSize(30).fontWeight(FontWeight.Bold).fontColor(INK)
            Text(this.hasPairing ? 'Windows 已配对' : 'Windows 未配对').fontSize(14).fontColor(MUTED)
          }.alignItems(HorizontalAlign.Start).layoutWeight(1)
          Button(this.hasPairing ? '重新配对' : '配对电脑', { type: ButtonType.Normal })
            .height(48).borderRadius(9).fontSize(14).backgroundColor(TINT).fontColor(NAVY)

              .border({ width: 2, color: BORDER }).shadow({ radius: 1, color: INK, offsetX: 2, offsetY: 4 })
            .enabled(!this.busy).stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => this.pair())
        }.width('100%')

        Column({ space: 10 }) {
          Row({ space: 8 }) {
            Text('待传输 ' + this.queueCount.toString() + ' 条')
              .fontSize(14).fontWeight(FontWeight.Bold).fontColor(INK).layoutWeight(1)
            Button(this.showRecords ? '返回收集' : '处理记录', { type: ButtonType.Normal })
              .height(48).borderRadius(9).backgroundColor(TINT).fontColor(NAVY)

              .border({ width: 2, color: BORDER }).shadow({ radius: 1, color: INK, offsetX: 2, offsetY: 4 }).fontSize(14)
              .stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => {
                this.showRecords = !this.showRecords;
                this.selected = undefined;
                this.scroller.scrollTo({ xOffset: 0, yOffset: 0 });
              })
            if (this.hasPairing) {
              Button('立即同步', { type: ButtonType.Normal })
                .height(48).borderRadius(9).backgroundColor(TINT).fontColor(NAVY)

              .border({ width: 2, color: BORDER }).shadow({ radius: 1, color: INK, offsetX: 2, offsetY: 4 }).fontSize(14)
                .enabled(!this.syncing && !this.busy).stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => this.flush())
            }
          }.width('100%')
          if (this.syncState) {
            Row({ space: 6 }) {
              Text(this.syncState).fontSize(14).fontColor(MUTED).constraintSize({ maxWidth: '90%' })
              Row().width(12).height(12).borderRadius(6).backgroundColor(this.connectionColor())
                .border({ width: 2, color: '#FFFFFF' }).accessibilityLevel('no')
            }
          }
          if (this.captureState) {
            Text(this.captureState).fontSize(14).fontColor(NAVY)
          }
        }.alignItems(HorizontalAlign.Start).width('100%').padding(12).borderRadius(14).backgroundColor(TINT)

        if (!this.showRecords) {
        Column({ space: 12 }) {
          Text('待收集内容').fontSize(19).fontWeight(FontWeight.Bold).fontColor(INK)
          if (this.parentId) {
            Button('← 改为新建收集', { type: ButtonType.Normal })
              .height(48).borderRadius(9).backgroundColor(TINT).fontColor(NAVY)
              .enabled(!this.busy).stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => this.leaveSupplement())
          }
          if (this.clipboardHint) {
            Text(this.clipboardHint).fontSize(14).fontColor(MUTED)
          }
          if (this.title || this.draft.trim() || this.files.length) {
            Text(capturePreview(this.title, this.draft)).fontSize(15).fontColor(INK)
              .maxLines(2).textOverflow({ overflow: TextOverflow.Ellipsis })
          }
          if (this.duplicate && !this.parentId) {
            Button('已收集过 · 查看或补充', { type: ButtonType.Normal })
              .height(48).fontColor(NAVY).backgroundColor(TINT)
              .stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => {
                if (this.duplicate) {
                  this.openDetail(this.duplicate);
                }
              })
          }
          TextArea({ placeholder: '粘贴或输入原帖链接、分享文字', text: this.draft })
            .height(96).width('100%').backgroundColor(SURFACE).fontSize(16).fontColor(INK)
            .border({ width: 2, color: BORDER }).borderRadius(9)
            .accessibilityText('待收集内容')
            .onChange((value: string) => this.draft = value)
          Row({ space: 12 }) {
            PasteButton({ icon: PasteIconStyle.LINES, text: PasteDescription.PASTE, buttonType: ButtonType.Normal })
              .height(48).borderRadius(12).backgroundColor(TINT).fontColor(NAVY).iconColor(INK)
              .borderWidth(2).borderColor(INK)
              .onClick((_event: ClickEvent, result: PasteButtonOnClickResult): void => {
                if (result === PasteButtonOnClickResult.SUCCESS && !this.busy) {
                  this.readClipboard();
                }
              })
            Button('添加图片或视频', { type: ButtonType.Normal })
              .height(48).borderRadius(9).fontSize(14).backgroundColor(TINT).fontColor(NAVY)

              .border({ width: 2, color: BORDER }).shadow({ radius: 1, color: INK, offsetX: 2, offsetY: 4 })
              .enabled(!this.busy).stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => this.selectFiles())
          }
          Button('上传 PDF / Word', { type: ButtonType.Normal })
            .height(48).borderRadius(9).fontSize(14).backgroundColor(TINT).fontColor(NAVY)
            .border({ width: 2, color: BORDER }).shadow({ radius: 1, color: INK, offsetX: 2, offsetY: 4 })
            .enabled(!this.busy && this.files.length < 64)
            .stateStyles({ normal: this.raisedButton, pressed: this.pressedButton })
            .onClick(() => this.selectDocuments())
          if (this.files.length) {
            Row({ space: 8 }) {
              Text('已选 ' + this.files.length.toString() + ' 个附件').fontSize(14).fontColor(MUTED).layoutWeight(1)
              Button('清除附件', { type: ButtonType.Normal }).height(48).fontSize(14)
                .backgroundColor(TINT).fontColor(NAVY)

              .border({ width: 2, color: BORDER }).shadow({ radius: 1, color: INK, offsetX: 2, offsetY: 4 }).stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => {
                  this.files = [];
                  this.complete = false;
                })
            }.width('100%')
            if (firstUrl(this.draft)) {
              Row({ space: 8 }) {
                Checkbox({ name: 'full-source' }).select(this.complete).selectedColor(NAVY)
                  .accessibilityText('正文和原始附件已齐全').responseRegion({ x: -12, y: -12, width: 48, height: 48 })
                  .onChange((checked: boolean) => this.complete = checked)
                Text('正文和原始附件已齐全').fontSize(14).fontColor(MUTED).layoutWeight(1)
              }.width('100%').height(48)
            }
          }
          Text('资料类型').fontSize(16).fontWeight(FontWeight.Bold).fontColor(INK)
          Row({ space: 8 }) {
            ForEach(TYPES.slice(0, 3), (value: string) => this.option(value, true))
          }.width('100%')
          Row({ space: 8 }) {
            ForEach(TYPES.slice(3), (value: string) => this.option(value, true))
          }.width('100%')
          if (this.category === '自定义') {
            TextInput({ placeholder: '输入资料类型', text: this.customCategory }).height(48)
              .maxLength(32).fontSize(16).backgroundColor(SURFACE).fontColor(INK)
              .border({ width: 2, color: BORDER }).borderRadius(9).accessibilityText('自定义资料类型')
              .onChange((value: string) => this.customCategory = value)
          }
          Text('重要程度').fontSize(16).fontWeight(FontWeight.Bold).fontColor(INK)
          Row({ space: 8 }) {
            ForEach(IMPORTANCE, (value: string) => this.option(value, false))
          }.width('100%')
          if (this.category.includes('图') || this.category === '配色' || this.drawingNotes) {
            Text('图型与绘图参数（可选）').fontSize(16).fontColor(INK)
            TextArea({ placeholder: '例如 图型：技术路线图；参数：横向布局、线宽 1 pt', text: this.drawingNotes })
              .height(96).maxLength(12000).fontSize(15).backgroundColor(SURFACE)
              .enabled(!this.busy).onChange((value: string) => this.drawingNotes = value)
          }
          if (this.errorText) {
            Text(this.errorText).fontSize(14).fontColor('#9F1239')
          }
          Button(this.busy ? '请稍候…' : (this.parentId ? '确认补充原记录' : '确认并加入队列'), { type: ButtonType.Normal })
            .width('100%').height(52).borderRadius(10).fontSize(16).backgroundColor(HONEY).fontColor(INK)
            .fontColor(INK).border({ width: 2, color: INK })
            .shadow({ radius: 1, color: INK, offsetX: 2, offsetY: 4 })
            .enabled(!this.busy && (this.draft.trim().length > 0 || this.files.length > 0))
            .stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => this.save())
        }.alignItems(HorizontalAlign.Start).width('100%').padding(16).borderRadius(14).backgroundColor(PAPER)
          .border({ width: 2, color: BORDER })
        } else if (this.selected) {
          Column({ space: 14 }) {
            Text(this.selected.title).fontSize(20).fontWeight(FontWeight.Bold).fontColor(INK)
            Text(this.selected.body ?? '').fontSize(15).fontColor(INK).copyOption(CopyOptions.LocalDevice)
            if (this.selected.url) {
              Button('打开原帖', { type: ButtonType.Normal }).height(48).backgroundColor(TINT).fontColor(INK)
                .border({ width: 2, color: INK }).borderRadius(9)
                .stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => { if (this.selected) { this.openOriginal(this.selected.url || ''); } })
              Text(this.selected.url).fontSize(14).fontColor(NAVY).copyOption(CopyOptions.LocalDevice)
            }
            Button('补充 / 修改分类').height(48).backgroundColor(HONEY).fontColor(INK)
              .stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => {
                if (this.selected) {
                  this.supplement(this.selected);
                }
              })
            Button('返回记录').height(48).backgroundColor(TINT).fontColor(NAVY)
              .stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(() => this.selected = undefined)
            Button('重新处理').height(48).backgroundColor(TINT).fontColor(NAVY)
              .stateStyles({ normal: this.raisedButton, pressed: this.pressedButton }).onClick(async (): Promise<void> => {
                if (this.selected) {
                  try {
                    await retryRecord(this.context().filesDir, this.selected.id);
                    this.captureState = '已提交重新处理，请稍后刷新记录';
                  } catch (error) {
                    this.captureState = connectionError(error as BusinessError);
                  }
                }
              })
          }.width('100%').alignItems(HorizontalAlign.Start).padding(16)
            .backgroundColor(PAPER).borderRadius(14)
        } else {
          if (this.records.length === 0) {
            Text('暂无记录').fontSize(14).fontColor(MUTED)
          }
          ForEach(this.records, (item: CaptureRecord) => {
            Column({ space: 16 }) {
              Row({ space: 12 }) {
              Text(this.statusLabel(item.status)).fontSize(12).fontWeight(FontWeight.Bold)
                .fontColor(this.statusColor(item.status)).backgroundColor(this.statusColor(item.status, true))
                .padding({ left: 9, right: 9, top: 5, bottom: 5 }).borderRadius(8)
                .border({ width: 1, color: this.statusColor(item.status) }).flexShrink(0)
                Text(item.category || '未分类').fontSize(12)
                  .fontColor(MUTED).layoutWeight(1).textAlign(TextAlign.End).maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
                Text('｜ ' + (item.importance || '中')).fontSize(12).fontWeight(FontWeight.Bold)
                  .fontColor(item.importance === '高' ? '#9D351F' : MUTED)
              }.width('100%')
              Text(this.displayTitle(item.title)).fontSize(18).fontWeight(FontWeight.Bold).fontColor(INK).maxLines(3)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Divider().color('#E8DCCC').strokeWidth(1)
              Row({ space: 8 }) {
                Text(item.details && item.details.length > 0 ? item.details[0] : '')
                  .fontSize(12).fontColor(MUTED).layoutWeight(1).maxLines(2)
                  .textOverflow({ overflow: TextOverflow.Ellipsis })
                Text('详情  ›').fontSize(12).fontColor(INK).fontWeight(FontWeight.Bold).textAlign(TextAlign.End)
              }.width('100%').alignItems(VerticalAlign.Bottom)
            }.alignItems(HorizontalAlign.Start).width('100%')
              .padding({ left: 18, right: 22, top: 16, bottom: 20 }).borderRadius(14)
              .backgroundColor(PAPER).border({ width: 2, color: INK })
              .shadow({ radius: 1, color: INK, offsetX: 2, offsetY: 4 })
              .accessibilityText((item.category || '未分类') + '｜' + (item.importance || '中') + '｜' + this.displayTitle(item.title) + '，' + this.statusLabel(item.status) + '，查看详情')
              .onClick(() => this.openDetail(item))
          }, (item: CaptureRecord): string => JSON.stringify(item))
        }
      }.alignItems(HorizontalAlign.Start).width('100%').constraintSize({ maxWidth: 580 })
        .padding({ left: 16, right: 16, top: 16, bottom: 24 })
    }.width('100%').height('100%').backgroundColor(SURFACE)
  }
}

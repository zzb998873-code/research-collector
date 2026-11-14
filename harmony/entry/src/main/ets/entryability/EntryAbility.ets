import { AbilityConstant, UIAbility, Want } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { systemShare } from '@kit.ShareKit';
import { SharedDraft, SharedFile } from '../collector/Queue';

export default class EntryAbility extends UIAbility {
  onCreate(want: Want, _launchParam: AbilityConstant.LaunchParam): void {
    this.receive(want);
  }

  onNewWant(want: Want, _launchParam: AbilityConstant.LaunchParam): void {
    this.receive(want);
  }

  onWindowStageCreate(stage: window.WindowStage): void {
    stage.loadContent('pages/Index');
  }

  onForeground(): void {
    AppStorage.setOrCreate('foregroundCount', (AppStorage.get<number>('foregroundCount') ?? 0) + 1);
  }

  private async receive(want: Want): Promise<void> {
    if (want.action !== 'ohos.want.action.sendData' && want.action !== 'ohos.want.action.sendMultipleData') {
      return;
    }
    AppStorage.setOrCreate('captureState', '正在读取分享…');
    try {
      const data: systemShare.SharedData = await systemShare.getSharedData(want);
      const records: systemShare.SharedRecord[] = data.getRecords();
      const text: string[] = [];
      const files: SharedFile[] = [];
      let title: string = '';
      records.forEach((record: systemShare.SharedRecord): void => {
        if (!title && record.title) {
          title = record.title;
        }
        if (record.content && !text.includes(record.content)) {
          text.push(record.content);
        }
        if (record.uri?.startsWith('file://')) {
          const uri: string = record.uri;
          const basename: string = uri.split('/').pop() ?? '';
          const named: string = record.title ?? '';
          files.push({ uri: uri, name: /\.[A-Za-z0-9]{2,5}$/.test(named) ? named : basename });
        } else if (record.uri?.match(/^https?:\/\//)) {
          const uri: string = record.uri;
          if (!text.includes(uri)) {
            text.push(uri);
          }
        }
      });
      if (!text.length && title && !files.length) {
        text.push(title);
      }
      const contents: string = text.join('\n');
      if (!contents && !files.length) {
        throw new Error('分享内容为空');
      }
      const draft: SharedDraft = { title: title, text: contents, files: files };
      AppStorage.setOrCreate('shareDraft', JSON.stringify(draft));
      AppStorage.setOrCreate('captureState', '');
    } catch (error) {
      AppStorage.setOrCreate('captureState', '未能读取分享 · ' + ((error as Error).message ?? '可以复制链接后粘贴'));
    }
  }
}

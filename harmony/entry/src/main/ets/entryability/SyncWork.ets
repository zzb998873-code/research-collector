import { WorkSchedulerExtensionAbility, workScheduler } from '@kit.BackgroundTasksKit';
import { schedule, sync } from '../collector/Queue';

export default class SyncWork extends WorkSchedulerExtensionAbility {
  onWorkStart(work: workScheduler.WorkInfo): void {
    this.flush(work);
  }

  onWorkStop(_work: workScheduler.WorkInfo): void {
  }

  private async flush(work: workScheduler.WorkInfo): Promise<void> {
    const root: string = this.context.filesDir;
    try {
      await sync(root, 65000);
    } catch {
      // The next scheduled run resumes from the last server-confirmed offset.
    } finally {
      schedule(root, work.bundleName);
    }
  }
}

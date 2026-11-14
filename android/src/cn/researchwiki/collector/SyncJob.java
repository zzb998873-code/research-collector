package cn.researchwiki.collector;

import android.app.job.JobParameters;
import android.app.job.JobService;

public final class SyncJob extends JobService {
    private volatile Thread worker;

    @Override
    public boolean onStartJob(JobParameters job) {
        worker = new Thread(() -> {
            boolean retry = false;
            try {
                CollectorStore store = new CollectorStore(this);
                if (store.size() > 0) store.sync(false);
                retry = store.paired() && store.size() > 0;
            } catch (Exception error) {
                retry = true;
            }
            if (!Thread.currentThread().isInterrupted()) {
                jobFinished(job, retry);
            }
        }, "collector-background");
        worker.start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters job) {
        if (worker != null) {
            worker.interrupt();
        }
        return true;
    }
}

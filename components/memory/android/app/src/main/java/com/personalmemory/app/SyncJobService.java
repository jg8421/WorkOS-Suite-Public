package com.personalmemory.app;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

public class SyncJobService extends JobService {
    private static final int JOB_ID = 18765;
    static void schedule(Context context) {
        try {
            JobScheduler scheduler = context.getSystemService(JobScheduler.class);
            if (scheduler == null || scheduler.getPendingJob(JOB_ID) != null) return;
            scheduler.schedule(new JobInfo.Builder(JOB_ID, new ComponentName(context, SyncJobService.class))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPeriodic(15 * 60 * 1000L)
                .setPersisted(true).build());
        } catch (RuntimeException ignored) {
            // Queue still retries on capture, network restoration, or opening the app.
        }
    }
    @Override public boolean onStartJob(JobParameters params) {
        MemoryClient.flush(this, (sent, pending, error) -> jobFinished(params, false));
        return true;
    }
    @Override public boolean onStopJob(JobParameters params) { return true; }
}

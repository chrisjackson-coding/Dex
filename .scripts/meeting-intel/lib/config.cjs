'use strict';

function getMeetingProcessingMode(meetingProcessing) {
  const mode = typeof meetingProcessing === 'string'
    ? meetingProcessing
    : meetingProcessing?.mode;
  return mode === 'manual' || mode === 'automatic' ? mode : 'manual';
}

function getMeetingBackfillDays(meetingProcessing) {
  const value = Number(
    typeof meetingProcessing === 'object' && meetingProcessing
      ? meetingProcessing.backfill_days
      : undefined,
  );
  return [7, 14, 30].includes(value) ? value : 14;
}

module.exports = { getMeetingProcessingMode, getMeetingBackfillDays };

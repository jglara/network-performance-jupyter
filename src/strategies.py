import json
import time
from typing import List, Dict, Optional


class SenderStrategy(object):
    def __init__(self) -> None:
        self.seq_num = 0
        self.next_ack = 0
        self.sent_bytes = 0
        self.start_time = time.time()
        self.total_acks = 0
        self.num_duplicate_acks = 0
        self.curr_duplicate_acks = 0
        self.rtts = []
        self.cwnds = []
        self.unacknowledged_packets = {}
        self.times_of_acknowledgements = []
        self.ack_count = 0
        self.slow_start_thresholds = []

    def next_packet_to_send(self):
        raise NotImplementedError

    def process_ack(self, ack: str):
        raise NotImplementedError


class TahoeStrategy(SenderStrategy):
    def __init__(self, slow_start_thresh: int, initial_cwnd: int) -> None:
        self.initial_cwnd = initial_cwnd
        self.slow_start_thresh = slow_start_thresh

        # List of tuples of ack_time and seq_num

        self.cwnd = initial_cwnd
        self.fast_retransmit_packet = None
        self.time_since_retransmit = None
        self.retransmitting_packet = False

        self.duplicated_ack = None

        super().__init__()

    def window_is_open(self) -> bool:
        # Returns true if the congestion window is not full

        return self.seq_num - self.next_ack < self.cwnd

    def next_packet_to_send(self) -> Optional[str]:
        send_data = None
        if self.retransmitting_packet and time.time() - self.time_of_retransmit > 1:
            self.retransmitting_packet = False

        if self.fast_retransmit_packet and not self.retransmitting_packet:
            self.unacknowledged_packets[self.fast_retransmit_packet['seq_num']]['send_ts'] = time.time()
            send_data = self.fast_retransmit_packet
            serialized_data = json.dumps(send_data)
            self.retransmitting_packet = True

            self.time_of_retransmit = time.time()

        elif self.window_is_open():
            send_data = {
                'seq_num': self.seq_num,
                'send_ts': time.time(),
                'sent_bytes': self.sent_bytes
            }

            self.unacknowledged_packets[self.seq_num] = send_data
            self.seq_num += 1
        else:
            for seq_num, segment in self.unacknowledged_packets.items():
                if time.time() - segment['send_ts'] > 4:
                    self.unacknowledged_packets[seq_num]['send_ts'] = time.time()
                    return json.dumps(segment)

        if send_data is None:
            return None
        else:
            return json.dumps(send_data)


    def process_ack(self, serialized_ack: str) -> None:

        ack = json.loads(serialized_ack)
        if ack.get('handshake'):
            return

        self.total_acks += 1
        self.times_of_acknowledgements.append(((time.time() - self.start_time), ack['seq_num']))


        if self.unacknowledged_packets.get(ack['seq_num']) is None:
            # Duplicate ack

            self.num_duplicate_acks += 1
            if self.duplicated_ack and ack['seq_num'] == self.duplicated_ack['seq_num']:
                self.curr_duplicate_acks += 1
            else:
                self.duplicated_ack = ack
                self.curr_duplicate_acks = 1

            if self.curr_duplicate_acks == 3:
                # Received 3 duplicate acks, retransmit
                self.fast_retransmit_packet = self.unacknowledged_packets[ack['seq_num'] + 1]
                self.slow_start_thresh = max(1, self.cwnd/2)
                self.cwnd = 1
        elif ack['seq_num'] >= self.next_ack:
            if self.fast_retransmit_packet:
                self.fast_retransmit_packet = None
                self.retransmitting_packet = False
                self.curr_duplicate_acks = 0
                self.seq_num = ack['seq_num'] + 1
                # Acknowledge all packets where seq_num < ack['seq_num']
                # Throw out all unacknowledged packets and start over

            # Acknowledge all packets where seq_num < ack['seq_num']
            self.unacknowledged_packets = {
                k:v
                for k,v in
                self.unacknowledged_packets.items()
                if k > ack['seq_num']
            }
            self.next_ack = max(self.next_ack, ack['seq_num'] + 1)
            self.sent_bytes += ack['ack_bytes']
            rtt = float(time.time() - ack['send_ts'])
            self.rtts.append(rtt)
            if self.cwnd < self.slow_start_thresh:
                # In slow start
                self.cwnd += 1

            elif (ack['seq_num'] + 1) % self.cwnd == 0:
                # In congestion avoidance
                self.cwnd += 1

        self.cwnds.append(self.cwnd)


class FixedWindowStrategy(SenderStrategy):
    def __init__(self, cwnd: int) -> None:
        self.cwnd = cwnd

        super().__init__()

    def window_is_open(self) -> bool:
        # Returns true if the congestion window is not full
        return self.seq_num - self.next_ack < self.cwnd

    def next_packet_to_send(self) -> Optional[str]:
        if not self.window_is_open():
            return None

        serialized_data = json.dumps({
            'seq_num': self.seq_num,
            'send_ts': time.time(),
            'sent_bytes': self.sent_bytes
        })
        self.unacknowledged_packets[self.seq_num] = True
        self.seq_num += 1
        return serialized_data

    def process_ack(self, serialized_ack: str) -> None:
        ack = json.loads(serialized_ack)
        if ack.get('handshake'):
            return

        self.total_acks += 1
        self.times_of_acknowledgements.append(((time.time() - self.start_time), ack['seq_num']))
        if self.unacknowledged_packets.get(ack['seq_num']) is None:
            # Duplicate ack
            self.num_duplicate_acks += 1
            self.curr_duplicate_acks += 1

            if self.curr_duplicate_acks == 3:
                # Received 3 duplicate acks, retransmit
                self.curr_duplicate_acks = 0
                self.seq_num = ack['seq_num'] + 1
        else:
            del self.unacknowledged_packets[ack['seq_num']]
            self.next_ack = max(self.next_ack, ack['seq_num'] + 1)
            self.sent_bytes += ack['ack_bytes']
            rtt = float(time.time() - ack['send_ts'])
            self.rtts.append(rtt)
            self.ack_count += 1
        self.cwnds.append(self.cwnd)

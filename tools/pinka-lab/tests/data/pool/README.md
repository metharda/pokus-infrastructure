# Test data of the Incus pool's count

Every file here is **hand-written**, in the formats of real output; none is
recorded from a host yet. They are to be replaced with recordings from a lab
host running Incus 6.0.7 on an LVM thin pool.

| File | Hand-written in the format of |
| --- | --- |
| `lvs.txt` | `lvs --noheadings --units b --nosuffix --separator , -o lv_name,lv_size,pool_lv,data_percent,metadata_percent,lv_metadata_size <vg>` |
| `incus-pool.json` | `incus query /1.0/storage-pools/<pool>` |
| `incus-projects.json` | `incus query /1.0/projects?recursion=1` |
| `incus-instances.json` | `incus query /1.0/instances?recursion=1&all-projects=true` (only the fields the count reads) |
| `incus-volumes.json` | `incus query /1.0/storage-pools/<pool>/volumes?recursion=1&all-projects=true` (only the fields the count reads) |

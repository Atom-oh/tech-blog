2026-10-21 managed kro 0.9.4-eks-1 + ACK 46.186.1-eks-1
- Existing ACK CRs in team-a: Queue "orders" (queueName eks-cap-demo-230de0-team-a-orders), Table "orders" (tableName eks-cap-demo-230de0-team-a-orders), created by kubectl.
- Created OrderService "orders" in team-a. kro server-side applied (applyset) onto both existing CRs with the same names; no ownership check/refusal.
- Queue: rejected, "spec.queueName: Value is immutable once set" -> instance state ERROR (after ~377s wait loop).
- Table: accepted; kro labels added (kro.run/owned=true), spec.tableName changed to eks-cap-demo-team-a-orders; ACK created a NEW table; old table eks-cap-demo-230de0-team-a-orders left in AWS, unmanaged (orphan).
- Deleted OrderService "orders": kro deleted the Table CR it had taken over (the pre-existing team-a/orders Table CR is gone), ACK deleted the new table. Original table eks-cap-demo-230de0-team-a-orders remains in AWS with no CR (orphan, delete at cleanup). Queue CR "orders" survived (kro apply had failed on it).
- OSS kro 0.9.4 reproduced the same takeover (Table dup -> tableName changed, original orphaned). Orphans deleted manually 09:50 UTC.

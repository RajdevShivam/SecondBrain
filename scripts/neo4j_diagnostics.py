"""
Neo4j Database Diagnostics Script
Runs read-only queries to assess the current state of the database.
"""

from neo4j import GraphDatabase
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Neo4j connection credentials
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")


def run_diagnostics():
    """Run diagnostic queries against the Neo4j database."""

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        with driver.session() as session:
            print("=" * 60)
            print("NEO4J DATABASE DIAGNOSTICS")
            print("=" * 60)

            # 1. Count total Concept nodes
            print("\n1. TOTAL CONCEPT NODES")
            print("-" * 40)
            result = session.run("MATCH (c:Concept) RETURN count(c) as count")
            record = result.single()
            concept_count = record["count"] if record else 0
            print(f"   Total Concept nodes: {concept_count}")

            # 2. Count total relationships
            print("\n2. TOTAL RELATIONSHIPS")
            print("-" * 40)
            result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
            record = result.single()
            rel_count = record["count"] if record else 0
            print(f"   Total relationships: {rel_count}")

            # Get relationship types breakdown
            result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as rel_type, count(r) as count
                ORDER BY count DESC
            """)
            print("\n   Relationship types breakdown:")
            for record in result:
                print(f"   - {record['rel_type']}: {record['count']}")

            # 3. Check if relationships have `first_linked` property
            print("\n3. RELATIONSHIPS WITH 'first_linked' PROPERTY")
            print("-" * 40)
            result = session.run("""
                MATCH ()-[r]->()
                WHERE r.first_linked IS NOT NULL
                RETURN count(r) as count
            """)
            record = result.single()
            first_linked_count = record["count"] if record else 0
            print(f"   Relationships with first_linked: {first_linked_count}")
            print(f"   Relationships without first_linked: {rel_count - first_linked_count}")

            # 4. Check if relationships have `last_updated` property
            print("\n4. RELATIONSHIPS WITH 'last_updated' PROPERTY")
            print("-" * 40)
            result = session.run("""
                MATCH ()-[r]->()
                WHERE r.last_updated IS NOT NULL
                RETURN count(r) as count
            """)
            record = result.single()
            last_updated_count = record["count"] if record else 0
            print(f"   Relationships with last_updated: {last_updated_count}")
            print(f"   Relationships without last_updated: {rel_count - last_updated_count}")

            # 5. Check if relationships have `confidence` property
            print("\n5. RELATIONSHIPS WITH 'confidence' PROPERTY")
            print("-" * 40)
            result = session.run("""
                MATCH ()-[r]->()
                WHERE r.confidence IS NOT NULL
                RETURN count(r) as count
            """)
            record = result.single()
            confidence_count = record["count"] if record else 0
            print(f"   Relationships with confidence: {confidence_count}")
            print(f"   Relationships without confidence: {rel_count - confidence_count}")

            # 6. Check for SyncState nodes
            print("\n6. SYNCSTATE NODES")
            print("-" * 40)
            result = session.run("MATCH (s:SyncState) RETURN count(s) as count")
            record = result.single()
            sync_count = record["count"] if record else 0
            print(f"   Total SyncState nodes: {sync_count}")

            if sync_count > 0:
                result = session.run("MATCH (s:SyncState) RETURN s LIMIT 5")
                print("\n   Sample SyncState nodes:")
                for record in result:
                    node = record["s"]
                    print(f"   - {dict(node)}")

            # 7. Sample relationships to see properties
            print("\n7. SAMPLE RELATIONSHIPS (showing properties)")
            print("-" * 40)
            result = session.run("""
                MATCH (a)-[r]->(b)
                RETURN a.name as from_node, type(r) as rel_type, b.name as to_node,
                       properties(r) as props
                LIMIT 10
            """)
            records = list(result)
            if records:
                for record in records:
                    print(f"\n   [{record['from_node']}] --{record['rel_type']}--> [{record['to_node']}]")
                    props = record['props']
                    if props:
                        for key, value in props.items():
                            print(f"      {key}: {value}")
                    else:
                        print("      (no properties)")
            else:
                print("   No relationships found to sample.")

            # 8. Check for orphan concepts (no relationships)
            print("\n8. ORPHAN CONCEPTS (nodes with no relationships)")
            print("-" * 40)
            result = session.run("""
                MATCH (c:Concept)
                WHERE NOT (c)--()
                RETURN count(c) as count
            """)
            record = result.single()
            orphan_count = record["count"] if record else 0
            print(f"   Orphan Concept nodes: {orphan_count}")

            if orphan_count > 0 and orphan_count <= 20:
                result = session.run("""
                    MATCH (c:Concept)
                    WHERE NOT (c)--()
                    RETURN c.name as name
                    LIMIT 20
                """)
                print("\n   Orphan concept names:")
                for record in result:
                    print(f"   - {record['name']}")
            elif orphan_count > 20:
                result = session.run("""
                    MATCH (c:Concept)
                    WHERE NOT (c)--()
                    RETURN c.name as name
                    LIMIT 10
                """)
                print(f"\n   First 10 orphan concept names (out of {orphan_count}):")
                for record in result:
                    print(f"   - {record['name']}")

            # 9. Additional: Check all node labels in database
            print("\n9. ALL NODE LABELS IN DATABASE")
            print("-" * 40)
            result = session.run("CALL db.labels()")
            labels = [record[0] for record in result]
            print(f"   Labels: {', '.join(labels) if labels else 'None'}")

            # Count per label
            for label in labels:
                result = session.run(f"MATCH (n:`{label}`) RETURN count(n) as count")
                record = result.single()
                count = record["count"] if record else 0
                print(f"   - {label}: {count} nodes")

            # 10. Check Concept node properties
            print("\n10. CONCEPT NODE PROPERTIES (sample)")
            print("-" * 40)
            result = session.run("""
                MATCH (c:Concept)
                RETURN c
                LIMIT 3
            """)
            records = list(result)
            if records:
                for record in records:
                    node = record["c"]
                    print(f"\n   Concept: {node.get('name', 'N/A')}")
                    for key, value in dict(node).items():
                        # Truncate long values
                        str_val = str(value)
                        if len(str_val) > 100:
                            str_val = str_val[:100] + "..."
                        print(f"      {key}: {str_val}")
            else:
                print("   No Concept nodes found.")

            print("\n" + "=" * 60)
            print("DIAGNOSTICS COMPLETE")
            print("=" * 60)

    finally:
        driver.close()


if __name__ == "__main__":
    run_diagnostics()

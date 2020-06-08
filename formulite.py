import aiosqlite
import os
from pydantic import BaseModel
import inspect
import math

# FORMUlite provides a simple ORM to perform asynchronous connection within an sqlite database.
# currently under development, there are probably several bugs just waiting to be found.

# Current Version 0.3
# Dependencies: Pydantic, aiosqlite, async-property

# Next features: tokens for safe connections, 'delete/remove' functions, more custom sql functions (maybe), allowing typedict customization, support for multiple inheritance

class formulite:
    typedict = {"str":"TEXT",
                "Optional[str]":"TEXT",
                "int":"INT",
                "Optional[int]":"INT"}

    # Here goes some singleton classes
    _manager_instance = None

    # Using vars() on a pydantic class can be kinda frustrating.
    # The couple next methods are a way to go around it.
    # They also remove the base class attributes that would be returned.
    @staticmethod
    def vars_keys(Obj): # for instances, use .__class__ to call this method.
        Sup = Obj.mro()[1]
        this = list(Obj.__fields__.keys())
        those = list(Sup.__fields__.keys())
        for item in those:
            this.remove(item)
        return this

    @staticmethod
    def vars_values(instance): # using an empty constructor will retrieve the class default values.
        Sup = instance.__class__.mro()[1]
        unwanted = list(Sup().__dict__.values())
        ret = list(instance.__dict__.values())
        for _ in range(len(unwanted)):
            ret.pop(0)
        return ret

    @staticmethod
    def paged_list(page_size, item_list, wrap=True):
        return Paged_list(page_size=page_size, item_list=item_list, wrap=wrap)

    # Note that the album turns out to be an "async constructor" because of its dependencies,
    # in contrast with paged_list. This partially hides its implementation
    @staticmethod
    async def album(page_size, album_Type, **kwargs):
        man = await formulite.manager()
        return Album(page_size=page_size, manage=man, album_Type=album_Type, **kwargs)

    # I thought of making this a singleton, but the user may want multiple instances
    @staticmethod
    def album_manager(limit=20):
        return AlbumManager(limit=limit)

    # Destroy the database file, if it exists.
    @staticmethod
    def clear_database(dbname='database.db'):
        if os.path.exists(dbname):
            os.remove(dbname)

    # internal method to get the single database manager instance
    @staticmethod
    async def _getInstance(dbname):
        if formulite._manager_instance is None:
            connection = await aiosqlite.connect(dbname)
            formulite._manager_instance = DatabaseManager(connection)
        return formulite._manager_instance

    # Get the single database manager instance (use this)
    @classmethod
    async def manager(cls, dbname='database.db'):
        return await cls._getInstance(dbname)

class DatabaseManager:
    def __init__(self, connection):
        self.conn = connection

    # should be called at the end of execution
    async def close(self):
        await self.conn.close()

    ### Auxiliary internal functions ###

    # tablenames are hidden from the user and should always be obtained through this function
    def _tablename(self, Obj):
        return Obj.__name__.lower() + "s" # s makes it plural, a dumb yet effective solution

    # This method returns true if the table does not exist in the database, and false otherwise
    async def _table_nonexistent(self, tname):
        c = await self.conn.execute(f"SELECT count(name) FROM sqlite_master WHERE type='table' AND name='{tname}'")
        test, = await c.fetchone()
        if test == 1:
            return False
        return True

    # This method acquires and returns object attributes in a dictionary. It does not return base class attributes.
    # Does not support multiple inheritance. (I should probably fix this)
    def _objprops(self, Obj):
        out = {}
        chaves = list(Obj.__fields__.keys())
        tipos = list(Obj.__fields__.values())
        for i in range(len(chaves)):
            out.update( {chaves[i]:str(tipos[i]._type_display())} )

        # Base class attributes are removed before returning
        Sup = Obj.mro()[1]
        if Sup != BaseModel:
            # So we remove them here in recursive style
            for k, v in self._objprops(Sup).items():
                del out[k]

        return out

    # This method is responsible for accessing the typedict dictionary and inferring sqlite types from pydantic types
    def _translate_type(self, pytype):
        return formulite.typedict[pytype]

    # This method needs a closer look
    # The goal is to insert the cursor.lastrowid property into the instance before adding it to the database, to act as a foreign key
    def _parse_instance(self, instance, cursor, Supper):
        vals = formulite.vars_values(instance)
        if Supper != BaseModel:
            vals.insert(0, cursor.lastrowid)
        return tuple(vals)

    # didn't neeeeed to be async but wynaut
    async def _extract_from_instance(self, Sup, instance):
        sup_args = {}
        y = list(vars(instance).values())
        x = list(vars(Sup()).keys())
        for i in range(len(x)):
            sup_args.update({ x[i]:y[i] })
        return Sup(**sup_args)

    ### CREATE ###

    # This method creates a table, very straightforward
    async def create_table(self, Obj):
        props = self._objprops(Obj)
        sql = f"CREATE TABLE IF NOT EXISTS {self._tablename(Obj)} ( {Obj.__name__.lower()}_id INTEGER PRIMARY KEY"

        Sup = Obj.mro()[1]
        sup_name = f"{Sup.__name__.lower()}"
        if Sup != BaseModel:
            sql += f", {sup_name}_id INTEGER" # NOT NULL"

        for key, value in list(props.items()):
            sql += f", {key} {self._translate_type(value)}"

        # check for superclass (again)
        if Sup != BaseModel:
            sql += f''', FOREIGN KEY ({sup_name}_id) REFERENCES {self._tablename(Sup)} ({sup_name}_id) ON UPDATE CASCADE ON DELETE CASCADE'''

        sql += " )"
        await self.conn.execute(sql)
        await self.conn.commit()

    ### READ ###
    # Many functions inside this category are similar, should study a way to join them together.

    # This method is the basic method for reading data from an sqlite table
    # if you also want to retrieve the superclass attributes, use search_joined instead.
    async def search(self, Obj, exact=True, **kargs):
        # 1 - Define which columns will be selected by the query
        att = formulite.vars_keys(Obj)
        rows = ", ".join(att) # Maybe could substitute for .Row object (read documentation)
        sql = f"SELECT {rows} FROM {self._tablename(Obj)}"

        # 2 - Define the criteria for the search results, and store them into fetch
        fetch = None
        if kargs:
            key, value = list(kargs.items())[0]
            c = None
            if not exact:
                c = await self.conn.execute(sql + f" WHERE {key} LIKE ?", (f"%{value}%",))
            else:
                c = await self.conn.execute(sql + f" WHERE {key} = ?", (value, ))
            fetch = await c.fetchall()
        else:
            fetch = await self.conn.execute_fetchall(sql)
            # sql = f"SELECT * FROM {self._tablename(Obj)}" is not a good idea here
            # because we may have more parameters than the ones that the object requires

        # 3 - Build the list of objects retrieved, and return it
        out = []
        for result in fetch:
            aux = {}
            for i in range(len(att)):
                aux.update( {att[i] : result[i]} )
            out.append(Obj(**aux))
        return out

    async def select_some(self, Obj, exact=True, limit=None, offset=None **kargs): # Unfinished
        # 1 - Define which columns will be selected by the query
        att = formulite.vars_keys(Obj)
        rows = ", ".join(att)
        sql = f"SELECT * FROM {self._tablename(Obj)}"

        # joined_columns is a list of all columns that appear on the table after the join is executed
        joined_columns = [f"{Obj.__name__.lower()}_id"]
        joined_columns.extend(formulite.vars_keys(Obj))

        for subc in Obj.__subclasses__():
            sql += f" LEFT JOIN {self._tablename(subc)} USING ({Obj.__name__.lower()}_id)"
            joined_columns.extend([f"{subc.__name__.lower()}_id"])
            joined_columns.extend(formulite.vars_keys(subc))

        # 2 - Define the criteria for the search results
        placeholders = []
        if kargs:
            sql += " WHERE "
            conditionals = []
            for key, value in kargs.items():
                if not key:
                    raise ValueError("Invalid keyword argument.")
                if exact:
                    conditionals.append(f"{key} = ?")
                    placeholders.append(value)
                else:
                    conditionals.append(f"{key} LIKE ?")
                    placeholders.append(f"%{value}%")
            sql += " AND ".join(conditionals)
        
        # 2.5 - Check some optional args
        if limit:
            sql += f" LIMIT {limit}"
        if offset:
            sql += f" OFFSET {offset}"

        # 3 - store results in fetch
        fetch = await self.conn.execute_fetchall(sql, tuple(placeholders))

        # 4 - Build the list of objects retrieved, and return it
        # I guess this should be a separate object builder function
        results = []
        for result in fetch:
            # ret is a dictionary of {column:value}
            ret = dict((joined_columns[i], result[i]) for i in range(len(joined_columns)))
            for subc in Obj.__subclasses__():
                # if the current result has the subclass id, then the object to be returned is of that subclass
                if ret.get(f"{subc.__name__.lower()}_id"):
                    results.append(subc(**ret))
        return results

    # joins subclass tables and returns the amount of items
    # Should be the same result as doing len() on a regular search
    async def count_joined(self, Obj, exact=True, **kargs): 
        # 1 - Define which columns will be selected by the query
        att = formulite.vars_keys(Obj)
        rows = ", ".join(att)
        sql = f"SELECT count(*) FROM {self._tablename(Obj)}"

        # joined_columns is a list of all columns that appear on the table after the join is executed
        joined_columns = [f"{Obj.__name__.lower()}_id"]
        joined_columns.extend(formulite.vars_keys(Obj))

        for subc in Obj.__subclasses__():
            sql += f" LEFT JOIN {self._tablename(subc)} USING ({Obj.__name__.lower()}_id)"
            joined_columns.extend([f"{subc.__name__.lower()}_id"])
            joined_columns.extend(formulite.vars_keys(subc))

        # 2 - Define the criteria for the search results
        placeholders = []
        if kargs:
            sql += " WHERE "
            conditionals = []
            for key, value in kargs.items():
                if not key:
                    raise ValueError("Invalid keyword argument.")
                if exact:
                    conditionals.append(f"{key} = ?")
                    placeholders.append(value)
                else:
                    conditionals.append(f"{key} LIKE ?")
                    placeholders.append(f"%{value}%")
            sql += " AND ".join(conditionals)

        # 3 - store result in fetch
        fetch = await self.conn.execute_fetchall(sql, tuple(placeholders))

        # 4 - return the amount of items retrieved (int)
        return fetch[0]['count(*)']

    # joins subclass tables and then searches
    async def search_joined(self, Obj, exact=True, **kargs):
        # 1 - Define which columns will be selected by the query
        att = formulite.vars_keys(Obj)
        rows = ", ".join(att)
        sql = f"SELECT * FROM {self._tablename(Obj)}"

        # joined_columns is a list of all columns that appear on the table after the join is executed
        joined_columns = [f"{Obj.__name__.lower()}_id"]
        joined_columns.extend(formulite.vars_keys(Obj))

        for subc in Obj.__subclasses__():
            sql += f" LEFT JOIN {self._tablename(subc)} USING ({Obj.__name__.lower()}_id)"
            joined_columns.extend([f"{subc.__name__.lower()}_id"])
            joined_columns.extend(formulite.vars_keys(subc))

        # 2 - Define the criteria for the search results
        placeholders = []
        if kargs:
            sql += " WHERE "
            conditionals = []
            for key, value in kargs.items():
                if not key:
                    raise ValueError("Invalid keyword argument.")
                if exact:
                    conditionals.append(f"{key} = ?")
                    placeholders.append(value)
                else:
                    conditionals.append(f"{key} LIKE ?")
                    placeholders.append(f"%{value}%")
            sql += " AND ".join(conditionals)
            sql += "COLLATE NOCASE"

        # 3 - store results in fetch
        fetch = await self.conn.execute_fetchall(sql, tuple(placeholders))

        # 4 - Build the list of objects retrieved, and return it
        # I guess this should be a separate object builder function
        results = []
        for result in fetch:
            # ret is a dictionary of {column:value}
            ret = dict((joined_columns[i], result[i]) for i in range(len(joined_columns)))
            for subc in Obj.__subclasses__():
                # if the current result has the subclass id, then the object to be returned is of that subclass
                if ret.get(f"{subc.__name__.lower()}_id"):
                    results.append(subc(**ret))
        return results

    ### UPDATE ###
    async def add_one(self, instance, propagate=False, _cursed=None):
        Obj = instance.__class__
        if await self._table_nonexistent(self._tablename(Obj)):
            await self.create_table(Obj)
        attrib = formulite.vars_keys(Obj)
        
        c = _cursed
        if c == None:
            c = await self.conn.cursor()

        Sup = Obj.mro()[1]
        if Sup != BaseModel:
            attrib.insert(0, f"{Sup.__name__.lower()}_id")
            if propagate:
                before = await self._extract_from_instance(Sup, instance)
                await self.add_one(before, _cursed=c)

        attributes = ", ".join(attrib)
        interr = ", ".join(["?"] * len(attrib))
        sql = f"INSERT INTO {self._tablename(Obj)} ({attributes}) VALUES ({interr})"
        
        await c.execute(sql, self._parse_instance(instance, c, Sup))
        await self.conn.commit()
        if c != _cursed:
            await c.close()

    async def add_many(self, instance_list, propagate=False):
        for instance in instance_list:
            await self.add_one(instance, propagate)

    async def _add_many(self, instance_list, propagate=False):
        if instance_list == []:
            # should raise some error here
            return
        Obj = instance_list[0].__class__
        if await self._table_nonexistent(self._tablename(Obj)):
            await self.create_table(Obj)
        attrib = formulite.vars_keys(Obj)

        Sup = Obj.mro()[1]
        if Sup != BaseModel:
            attrib.insert(0, f"{Sup.__name__.lower()}_id")
            if propagate:
                before = [await self._extract_from_instance(Sup, instance) for instance in instance_list]
                await self.add_many(before)

        attributes = ", ".join(attrib)
        interr = ", ".join(["?"] * len(attrib))
        sql = f"INSERT INTO {self._tablename(Obj)} ({attributes}) VALUES ({interr})"

        c = await self.conn.cursor()
        parsed_list = [self._parse_instance(instance, c, Sup) for instance in instance_list]
        await c.executemany(sql, parsed_list)
        await self.conn.commit()
        await c.close()

    async def update_attribute(self, Obj, cname, change): # change should be a {old:new} dictionary
        await self.conn.execute(f"UPDATE {self._tablename(Obj)} SET {cname}={list(change.values())[0]} WHERE {cname} is {list(change.keys())[0]}")
        await self.conn.commit()

    async def update_item(self, instance, **kargs): # pass in an updated instance and the old values go in kargs
        Obj = instance.__class__
        attrib = formulite.vars_keys(Obj)
        vals = formulite.vars_values(instance)
        it = 0
        for _ in range(len(attrib)):
            if vals[it] == None:
                vals.pop(it)
                attrib.pop(it)
            else:
                it += 1
        changes = [f"{attrib[i]}='{vals[i]}'" for i in range(len(attrib))]
        changes_string = ", ".join(changes)
        k = list(kargs.keys())[0]
        v = list(kargs.values())[0]
        await self.conn.execute(f"UPDATE {self._tablename(Obj)} SET {changes_string} WHERE {k}={v}")
        await self.conn.commit()

    async def update_item_joined(self, Sup, instance, **kargs):
        pass # yet to be implemented 

    ### DELETE ###
    # async def remove_item(self, instance): # yet to be implemented

    ### OTHER ### - I mean, the user could run the aiosqlite library for these, maybe I'll drop this idea.
    async def custom_fetchall(self, sql, params=None):
        if params:
            return await self.conn.execute_fetchall(sql, params)
        else:
            return await self.conn.execute_fetchall(sql)